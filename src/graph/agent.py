"""Workflow-sandbox tool-calling agent (docs/WORKFLOW_SANDBOX.md §3).

One `create_react_agent` + one tool (`retrieve`) + two post-hoc safety
layers (citation parse in `parse.py`, topical verifier in `verifier.py`).

Two public entry points:

  - `run_agent(state)`        — non-streaming. Used by `scripts/smoke_run.py`
                                and the test suite.
  - `build_agent(grade, ...)` + `finalize_agent_run(...)` — for callers
                                that need to stream events themselves
                                (the Chainlit UI in `src/ui/app.py`).

Refusal shape (§3): when the agent declines or hits the `max_tool_calls`
ceiling, it speaks in its own voice and suggests topics drawn from
whatever chunks have already been retrieved. Ceiling-hit and verifier-
rejection produce the same student-facing experience.

`recursion_limit` is sized at `2 * max_tool_calls + 3` so the prebuilt
agent gets exactly N retrieve→tool turns plus one final assistant
message before the graph raises `GraphRecursionError`. The raise is the
ceiling-hit signal — we catch it and synthesise the refusal here.

Backend=fake: the agent uses whatever model the caller binds via
`get_llm()`. Tests pass a `FakeMessagesListChatModel` queued with the
AIMessages they want to exercise (see `tests/test_agent.py`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
)
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from src.config import settings
from src.graph.client import get_llm
from src.graph.logging import log_query, timed
from src.graph.parse import parse_citations
from src.graph.prompts import render
from src.graph.state import AgentState, Chunk, HistoryTurn, ToolCallRecord
from src.graph.tools import (
    get_recorded_tool_calls,
    retrieve,
    set_request_context,
)
from src.graph.verifier import verify_topical

log = logging.getLogger("aleem.graph.agent")


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


# Subjects whose textbooks are written in English. Everything else
# (islamic_studies, social_studies, digital_skills, arabic, math, …) is an
# Arabic-language textbook, so retrieve() queries must be Arabic to match the
# embedded chunk language regardless of what language the student asks in.
_ENGLISH_SUBJECTS = frozenset({"english"})


def book_language(subject: str) -> str:
    return "English" if subject in _ENGLISH_SUBJECTS else "Arabic"


def _system_prompt(grade: int, subject: str) -> str:
    return render(
        "agent.j2",
        grade=grade,
        subject=subject,
        book_language=book_language(subject),
        max_tool_calls=settings.agent.max_tool_calls,
    )


def history_to_messages(history: Sequence[HistoryTurn]) -> list[BaseMessage]:
    out: list[BaseMessage] = []
    for turn in history:
        if turn["role"] == "user":
            out.append(HumanMessage(content=turn["content"]))
        else:
            out.append(AIMessage(content=turn["content"]))
    return out


def build_agent(
    grade: int,
    subject: str,
    llm: BaseChatModel | None = None,
):
    """Compose a fresh `create_react_agent` bound to the current request.

    A new agent is built per turn so the system prompt can reference the
    current grade/subject. Cheap — the compile step doesn't load weights.
    """
    model = llm if llm is not None else get_llm(
        temperature=settings.llm.generation_temperature,
        for_agent=True,
    )
    return create_react_agent(
        model=model,
        tools=[retrieve],
        prompt=_system_prompt(grade, subject),
    )


def recursion_limit() -> int:
    """`2 * max_tool_calls + 3` — the ceiling-hit boundary for the agent."""
    return 2 * settings.agent.max_tool_calls + 3


# ---------------------------------------------------------------------------
# Refusal-shape helpers (§3)
# ---------------------------------------------------------------------------


def _unique_lesson_titles(
    tool_calls: Sequence[ToolCallRecord], cap: int = 3
) -> list[str]:
    seen: set[str] = set()
    titles: list[str] = []
    for tc in tool_calls:
        for chunk in tc.chunks:
            title = chunk.lesson_title.strip()
            if not title or title in seen:
                continue
            seen.add(title)
            titles.append(title)
            if len(titles) >= cap:
                return titles
    return titles


def refusal_message(tool_calls: Sequence[ToolCallRecord]) -> str:
    """Agent-voiced refusal (§3): own wording + topic suggestions from
    already-retrieved chunks. Same shape whether the trigger is
    "off_topic" or "ceiling_hit"."""
    titles = _unique_lesson_titles(tool_calls)
    if titles:
        suggestions = ", ".join(f"“{t}”" for t in titles)
        return (
            "I don't see this specific topic in the chunks I found in your "
            f"textbook. The closest material covers {suggestions} — want "
            "me to dive into one of those instead?"
        )
    return (
        "I don't have textbook material that covers this question. Try "
        "rephrasing or asking about a specific lesson and I'll search again."
    )


# ---------------------------------------------------------------------------
# Finalization (parse → verifier → assemble update dict)
# ---------------------------------------------------------------------------


def last_ai_text(messages: Sequence[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                ]
                return "".join(parts).strip()
    return ""


def _all_chunks(tool_calls: Sequence[ToolCallRecord]) -> list[Chunk]:
    flat: list[Chunk] = []
    for tc in tool_calls:
        flat.extend(tc.chunks)
    return flat


async def finalize_agent_run(
    state: AgentState,
    *,
    final_messages: Sequence[BaseMessage],
    ceiling_hit: bool,
) -> dict:
    """Run citation parse + topical verifier and assemble the state update.

    Shared by `run_agent()` (non-streaming) and `src/ui/app.py` (streaming).
    Caller is responsible for already having driven the agent loop and
    captured `final_messages`. Tool-call records are read from the
    per-request context var that `set_request_context()` populated.
    """
    user_query = state["user_query"]
    tool_calls = get_recorded_tool_calls()

    if ceiling_hit:
        return {
            "final_answer": refusal_message(tool_calls),
            "tool_calls": tool_calls,
            "citations": [],
            "citation_flags": [],
            "refused": True,
            "refusal_reason": "ceiling_hit",
            "verifier_verdict": "skipped",
            "verifier_reason": "skipped due to ceiling hit",
            "debug": {"latency_ms": {"verifier": 0}},
        }

    answer = last_ai_text(final_messages)
    chunks = _all_chunks(tool_calls)
    citations, flags = parse_citations(answer, chunks)

    verifier_latency_ms = 0
    verifier_verdict = "skipped"
    verifier_reason: str

    if settings.verifier.enabled and chunks:
        v_t0 = time.perf_counter()
        verdict = await verify_topical(user_query, answer, chunks)
        verifier_latency_ms = int((time.perf_counter() - v_t0) * 1000)
        if not verdict.on_topic:
            return {
                "final_answer": refusal_message(tool_calls),
                "tool_calls": tool_calls,
                "citations": [],
                "citation_flags": flags,
                "refused": True,
                "refusal_reason": "off_topic",
                "verifier_verdict": "off_topic",
                "verifier_reason": verdict.reason,
                "debug": {"latency_ms": {"verifier": verifier_latency_ms}},
            }
        verifier_verdict = "on_topic"
        verifier_reason = verdict.reason
    else:
        verifier_reason = (
            "no retrieve calls"
            if not chunks
            else "verifier disabled"
        )

    return {
        "final_answer": answer,
        "tool_calls": tool_calls,
        "citations": citations,
        "citation_flags": flags,
        "refused": False,
        "refusal_reason": "",
        "verifier_verdict": verifier_verdict,
        "verifier_reason": verifier_reason,
        "debug": {"latency_ms": {"verifier": verifier_latency_ms}},
    }


# ---------------------------------------------------------------------------
# Non-streaming entry point (tests + smoke_run)
# ---------------------------------------------------------------------------


@timed("agent_loop")
async def _run_loop(state: AgentState, *, llm: BaseChatModel | None = None) -> dict:
    """Drive the agent loop end-to-end via ainvoke, then finalize."""
    grade = state["grade"]
    subject = state["subject"]
    history = state.get("history") or []

    set_request_context(grade, subject)
    agent = build_agent(grade, subject, llm=llm)

    messages: list[BaseMessage] = [
        *history_to_messages(history),
        HumanMessage(content=state["user_query"]),
    ]

    ceiling_hit = False
    final_messages: Sequence[BaseMessage] = messages

    try:
        result = await agent.ainvoke(
            {"messages": messages},
            config={"recursion_limit": recursion_limit()},
        )
        final_messages = result.get("messages", messages)
    except GraphRecursionError:
        ceiling_hit = True
        log.info(
            "agent hit recursion limit (max_tool_calls=%d) — refusing",
            settings.agent.max_tool_calls,
        )

    return await finalize_agent_run(
        state,
        final_messages=final_messages,
        ceiling_hit=ceiling_hit,
    )


async def run_agent(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
) -> AgentState:
    """Run one turn end-to-end. The `llm` kwarg is for tests — production
    code calls it without arguments and gets the configured backend.
    """
    t0 = time.perf_counter()
    update = await _run_loop(state, llm=llm)
    total_ms = int((time.perf_counter() - t0) * 1000)

    debug = dict(state.get("debug") or {})
    debug.update(update.get("debug") or {})
    latency_ms = dict(debug.get("latency_ms") or {})
    latency_ms["total"] = total_ms
    debug["latency_ms"] = latency_ms

    merged: AgentState = {**state, **update, "debug": debug}  # type: ignore[typeddict-item]

    try:
        log_query(merged)
    except Exception:  # noqa: BLE001 — logging must never break the pipeline
        log.exception("log_query failed")

    return merged


__all__ = [
    "build_agent",
    "finalize_agent_run",
    "history_to_messages",
    "last_ai_text",
    "recursion_limit",
    "refusal_message",
    "run_agent",
]
