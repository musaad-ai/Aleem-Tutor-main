"""Aleem — Chainlit UI driving the workflow-sandbox tool-calling agent.

Wiring per docs/WORKFLOW_SANDBOX.md §3 / §7:

  - Tool calls become ephemeral status updates (`Searching: "<query>"`).
  - The agent's chat-model stream pipes tokens into the live answer
    message (same `astream_events` plumbing the old per-node UI used —
    only the node-name filter changes).
  - After the stream closes, `finalize_agent_run()` runs citation parse
    + topical verifier + JSONL logging, and citation cards are attached
    to the answer.

Graph internals stay pure — this handler is the only place LangGraph
events meet Chainlit primitives.

Run from `src/ui/`:

    cd src/ui && PYTHONPATH=../.. uv run chainlit run app.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# When this file is run by Chainlit from `src/ui/`, the repo root is two
# levels up. Keep `from src...` imports resolvable without requiring
# PYTHONPATH (the explicit PYTHONPATH=../.. in the run command remains
# the documented convention; this is a belt-and-suspenders fallback).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import chainlit as cl  # noqa: E402
import chainlit.data as cl_data  # noqa: E402
from chainlit.input_widget import Select  # noqa: E402
from chainlit.types import ThreadDict  # noqa: E402
from langchain_core.messages import BaseMessage, HumanMessage  # noqa: E402
from langgraph.errors import GraphRecursionError  # noqa: E402

from src.config import settings  # noqa: E402
from src.graph.agent import (  # noqa: E402
    build_agent,
    finalize_agent_run,
    history_to_messages,
    recursion_limit,
)
from src.graph.logging import log_query  # noqa: E402
from src.graph.state import initial_agent_state  # noqa: E402
from src.graph.tools import set_request_context  # noqa: E402
from src.retrieval.prewarm import prewarm_models_in_background  # noqa: E402
from src.ui.persistence import (  # noqa: E402
    make_data_layer,
    parse_thread_metadata,
    rebuild_history,
)

# Kick off Jina embedder + reranker loads in a daemon thread at module
# import so the first retrieve() doesn't block the asyncio loop on a
# ~15s cold model load (which freezes the Chainlit status bar). Gated
# on at least one populated grade collection — see prewarm.py.
prewarm_models_in_background()

# Subject options. Each entry is (internal_key, bilingual_label, short_en).
# Subject is chosen from the top chat-profile dropdown (one profile per
# subject); `short_en` is the dropdown/profile name and the chip label.
SUBJECTS: list[tuple[str, str, str]] = [
    ("islamic_studies", "الدراسات الإسلامية  ·  Islamic Studies", "Islamic Studies"),
    ("social_studies",  "الاجتماعيات  ·  Social Studies",        "Social Studies"),
    ("english",         "اللغة الإنجليزية  ·  English",            "English"),
    ("digital_skills",  "المهارات الرقمية  ·  Digital Skills",     "Digital Skills"),
]

# Grade options. Each entry is (grade_int, bilingual_label). Grade is the
# sticky "user setting": chosen in the ⚙ panel and remembered across chats
# (new chats default to the grade of the user's most recent thread).
GRADES: list[tuple[int, str]] = [
    (4,  "الصف الرابع  ·  Grade 4"),
    (7,  "الصف السابع  ·  Grade 7"),
    (8,  "الصف الثاني المتوسط  ·  Grade 8"),
    (10, "الصف العاشر  ·  Grade 10"),
]
GRADE_LABELS = [label for _, label in GRADES]
DEFAULT_GRADE = GRADES[0][0]


# The prebuilt agent's internal nodes are named "agent" (the LLM call)
# and "tools" (the tool execution). We stream tokens from "agent" and
# surface tool-call invocations from "tools".
AGENT_LLM_NODE = "agent"


def _profile_name(key: str) -> str:
    for k, _, short in SUBJECTS:
        if k == key:
            return short
    return key


def _profile_name_to_key(name: str) -> str:
    for key, _, short in SUBJECTS:
        if short == name:
            return key
    return SUBJECTS[0][0]


def _key_to_label(key: str) -> str:
    for k, lbl, _ in SUBJECTS:
        if k == key:
            return lbl
    return key


def _grade_label_to_int(label: str) -> int:
    for g, lbl in GRADES:
        if lbl == label:
            return g
    return DEFAULT_GRADE


def _grade_to_label(grade: int) -> str:
    for g, lbl in GRADES:
        if g == grade:
            return lbl
    return GRADE_LABELS[0]


def _grade_index(grade: int) -> int:
    for i, (g, _) in enumerate(GRADES):
        if g == grade:
            return i
    return 0


async def _persist_thread_metadata(**fields: object) -> None:
    """Merge fields into the current thread's metadata.

    SQLAlchemyDataLayer.update_thread merges with the existing row (it
    SELECTs first), so passing one field at a time is safe — it won't
    blow away the others. Wrapped in try/except so a storage hiccup
    never blocks the live chat.
    """
    layer = cl_data.get_data_layer()
    if layer is None:
        return
    thread_id = cl.context.session.thread_id
    if not thread_id:
        return
    try:
        await layer.update_thread(thread_id=thread_id, metadata=dict(fields))
    except Exception:  # noqa: BLE001 — never let bookkeeping break the UI
        pass


async def _last_used_grade() -> int | None:
    """Grade from the user's most recently-active thread, if any.

    Lets grade behave like a sticky user setting without a dedicated
    user-prefs store: we already tag every thread's metadata with its
    grade, so the newest thread's grade is the user's last choice.
    `get_all_user_threads` orders by last activity, so the first thread
    carrying a grade is the most recent one.
    """
    layer = cl_data.get_data_layer()
    if layer is None:
        return None
    user = cl.user_session.get("user")
    user_id = getattr(user, "id", None)
    if not user_id:
        return None
    try:
        threads = await layer.get_all_user_threads(user_id=user_id)
    except Exception:  # noqa: BLE001 — fall back to default grade on any error
        return None
    for thread in threads or []:
        grade = parse_thread_metadata(thread.get("metadata")).get("grade")
        if grade is not None:
            try:
                return int(grade)
            except (TypeError, ValueError):
                continue
    return None


def _grade_settings(grade: int) -> cl.ChatSettings:
    return cl.ChatSettings(
        [
            Select(
                id="grade",
                label="Grade  ·  الصف",
                values=GRADE_LABELS,
                initial_index=_grade_index(grade),
            ),
        ]
    )


def _make_context_chip(grade: int, subject: str) -> cl.CustomElement:
    return cl.CustomElement(
        name="ContextChip",
        display="inline",
        props={"grade": str(grade), "subject": _profile_name(subject)},
    )


async def _refresh_context_chip(grade: int, subject: str) -> None:
    """Update the pinned Grade·Subject chip in place (no-op if absent)."""
    chip = cl.user_session.get("context_chip")
    if chip is None:
        return
    chip.props = {"grade": str(grade), "subject": _profile_name(subject)}
    try:
        await chip.update()
    except Exception:  # noqa: BLE001 — chip is cosmetic, never block the chat
        pass


def _welcome_content(grade: int, subject: str) -> str:
    return (
        f"**أهلاً بك في عليم  ·  Welcome to Aleem**\n\n"
        f"- **Subject  ·  المادة:** {_key_to_label(subject)} "
        f"— pick another from the dropdown at the top.\n"
        f"- **Grade  ·  الصف:** {grade} "
        f"— change it in the ⚙ panel; it's remembered next time.\n\n"
        f"Ask your question — every answer will come from your Grade "
        f"{grade} textbook only."
    )


async def _refresh_welcome(grade: int, subject: str) -> None:
    """Re-render the stored welcome message after a setting changes."""
    msg = cl.user_session.get("welcome_msg")
    if msg is None:
        return
    msg.content = _welcome_content(grade, subject)
    try:
        await msg.update()
    except Exception:  # noqa: BLE001 — cosmetic, never block the chat
        pass


@cl.data_layer
def _data_layer():
    return make_data_layer()


@cl.header_auth_callback
def _header_auth(headers) -> cl.User | None:
    # Local-only build: a single hardcoded user so the data layer has
    # someone to scope threads to. Replace with real auth at deploy time.
    return cl.User(identifier="local", metadata={"role": "local"})


@cl.set_chat_profiles
async def chat_profiles() -> list[cl.ChatProfile]:
    return [
        cl.ChatProfile(
            name="Islamic Studies",
            markdown_description=(
                "**الدراسات الإسلامية  ·  Islamic Studies**\n\n"
                "Aleem will answer only from your Ministry of Education "
                "Islamic Studies textbook. Set your grade in the ⚙ panel."
            ),
        ),
        cl.ChatProfile(
            name="Social Studies",
            markdown_description=(
                "**الاجتماعيات  ·  Social Studies**\n\n"
                "Aleem will answer only from your Ministry of Education "
                "Social Studies textbook. Set your grade in the ⚙ panel."
            ),
        ),
        cl.ChatProfile(
            name="English",
            markdown_description=(
                "**اللغة الإنجليزية  ·  English**\n\n"
                "Aleem will answer only from your Ministry of Education "
                "English textbook. Set your grade in the ⚙ panel."
            ),
        ),
        cl.ChatProfile(
            name="Digital Skills",
            markdown_description=(
                "**المهارات الرقمية  ·  Digital Skills**\n\n"
                "Aleem will answer only from your Ministry of Education "
                "Digital Skills textbook. Set your grade in the ⚙ panel."
            ),
        ),
    ]


@cl.on_chat_start
async def on_chat_start() -> None:
    # Subject comes from the chat-profile dropdown at the top.
    profile = cl.user_session.get("chat_profile")  # e.g. "Islamic Studies"
    subject = _profile_name_to_key(str(profile))
    cl.user_session.set("subject", subject)
    cl.user_session.set("history", [])

    # Grade is the sticky user setting: default to the user's last-used
    # grade (from their most recent thread), else the lowest grade.
    grade = await _last_used_grade()
    if grade is None:
        grade = DEFAULT_GRADE
    cl.user_session.set("grade", grade)

    await _grade_settings(grade).send()

    # Tag the thread so on_chat_resume can restore grade/subject without
    # walking the messages, and so _last_used_grade can read it back for
    # the next chat. Neither value is recoverable from the transcript.
    await _persist_thread_metadata(grade=grade, subject=subject)

    # Pin a Grade·Subject chip that stays visible while the chat is open.
    chip = _make_context_chip(grade, subject)
    cl.user_session.set("context_chip", chip)

    welcome_msg = cl.Message(
        content=_welcome_content(grade, subject),
        elements=[chip],
    )
    cl.user_session.set("welcome_msg", welcome_msg)
    await welcome_msg.send()


@cl.on_settings_update
async def on_settings_update(chat_settings: dict) -> None:
    label = chat_settings.get("grade")
    if label:
        grade = _grade_label_to_int(label)
        subject = cl.user_session.get("subject")
        cl.user_session.set("grade", grade)
        await _persist_thread_metadata(grade=grade)
        await _refresh_context_chip(grade, subject)
        await _refresh_welcome(grade, subject)


@cl.on_chat_resume
async def on_chat_resume(thread: ThreadDict) -> None:
    """Rehydrate cl.user_session from a persisted thread.

    Chainlit replays the transcript itself; this hook only re-fills the
    three values the pipeline reads from cl.user_session: grade,
    subject, and history. Grade & subject come from thread metadata
    (we tagged them at on_chat_start); history is rebuilt from the
    persisted user/assistant messages so the agent sees prior turns.
    """
    metadata = parse_thread_metadata(thread.get("metadata"))

    # Subject: prefer metadata; fall back to the chat-profile name.
    subject = metadata.get("subject")
    if subject is None:
        subject = _profile_name_to_key(str(cl.user_session.get("chat_profile")))
    cl.user_session.set("subject", subject)

    # Grade: prefer metadata; fall back to last-used, then default.
    grade = metadata.get("grade")
    if grade is None:
        grade = await _last_used_grade()
    if grade is None:
        grade = DEFAULT_GRADE
    grade = int(grade)
    cl.user_session.set("grade", grade)

    # History: rebuild from persisted messages, capped to the rewrite
    # node's window so we don't blow the prompt budget.
    history = rebuild_history(thread, settings.memory.max_turns)
    cl.user_session.set("history", history)

    # Re-send the grade widget so the ⚙ panel reflects the persisted choice.
    await _grade_settings(grade).send()

    # The pinned chip is replayed from the persisted welcome message, so we
    # don't re-send it here (that would double it). It won't live-update
    # after a resume — a cosmetic-only limitation.


def _citation_elements(final_state: dict) -> list[cl.Text]:
    """Build expandable source cards from the parsed citations."""
    elements: list[cl.Text] = []
    seen: set[int] = set()
    for cit in (final_state.get("citations") or []):
        if cit.n in seen:
            continue
        seen.add(cit.n)
        chunk = cit.chunk
        elements.append(
            cl.Text(
                name=f"[{cit.n}] {chunk.lesson_title} · p.{chunk.page}",
                content=chunk.text,
                display="side",
            )
        )
    return elements


@cl.on_message
async def on_message(message: cl.Message) -> None:
    grade = cl.user_session.get("grade")
    subject = cl.user_session.get("subject")
    history = cl.user_session.get("history") or []

    if grade is None or subject is None:
        await cl.Message(
            content=(
                "Please pick a subject from the dropdown at the top and a "
                "grade in the ⚙ panel before asking a question."
            ),
        ).send()
        return

    state = initial_agent_state(
        grade=grade,
        subject=subject,
        user_query=message.content,
        history=history,
    )

    # Ephemeral status — updates with each tool call, removed once the
    # answer is ready so it doesn't persist in the transcript.
    status_msg = cl.Message(content="⏳ Thinking…", author="Aleem")
    await status_msg.send()

    # Answer message — tokens stream into this from the agent's LLM node.
    answer_msg = cl.Message(content="")
    await answer_msg.send()

    set_request_context(grade, subject)
    agent = build_agent(grade, subject)

    messages: list[BaseMessage] = [
        *history_to_messages(history),
        HumanMessage(content=message.content),
    ]

    ceiling_hit = False
    final_messages: list[BaseMessage] = list(messages)
    t0 = time.perf_counter()
    agent_loop_ms = 0

    try:
        async for ev in agent.astream_events(
            {"messages": messages},
            version="v2",
            config={"recursion_limit": recursion_limit()},
        ):
            et = ev["event"]
            name = ev["name"]
            md = ev.get("metadata") or {}

            # Tool-call surfacing — show the verbatim query.
            if et == "on_tool_start" and name == "retrieve":
                inputs = ev["data"].get("input") or {}
                query = inputs.get("query", "")
                status_msg.content = f"🔎 Searching: “{query}”"
                await status_msg.update()

            # Token streaming from the agent LLM node.
            if (
                et == "on_chat_model_stream"
                and md.get("langgraph_node") == AGENT_LLM_NODE
            ):
                chunk = ev["data"].get("chunk")
                content = getattr(chunk, "content", None)
                if isinstance(content, str) and content:
                    await answer_msg.stream_token(content)
                elif isinstance(content, list):
                    for part in content:
                        if (
                            isinstance(part, dict)
                            and isinstance(part.get("text"), str)
                            and part["text"]
                        ):
                            await answer_msg.stream_token(part["text"])

            # Capture the final message list from the root chain's end.
            if et == "on_chain_end" and name == "LangGraph":
                output = ev["data"].get("output")
                if isinstance(output, dict) and "messages" in output:
                    final_messages = list(output["messages"])

    except GraphRecursionError:
        ceiling_hit = True
    except Exception as exc:  # noqa: BLE001 — surface any failure visibly per L20
        try:
            await status_msg.remove()
        except Exception:  # noqa: BLE001
            pass
        await cl.Message(
            content=f"⚠️ **Pipeline error**\n\n```\n{exc}\n```"
        ).send()
        return
    finally:
        agent_loop_ms = int((time.perf_counter() - t0) * 1000)

    # Run post-hoc verifier + citation parse using the same finalize
    # helper that run_agent() uses.
    update = await finalize_agent_run(
        state,
        final_messages=final_messages,
        ceiling_hit=ceiling_hit,
    )

    # Merge update + per-phase latency into the state for L18 logging.
    debug = dict(state.get("debug") or {})
    debug.update(update.get("debug") or {})
    latency_ms = dict(debug.get("latency_ms") or {})
    latency_ms["agent_loop"] = agent_loop_ms
    latency_ms["total"] = int((time.perf_counter() - t0) * 1000)
    debug["latency_ms"] = latency_ms

    final_state: dict = {**state, **update, "debug": debug}

    try:
        log_query(final_state)
    except Exception:  # noqa: BLE001 — logging must never break the UI
        pass

    # Drop the ephemeral status line.
    try:
        await status_msg.remove()
    except Exception:  # noqa: BLE001
        pass

    final_answer = final_state.get("final_answer", "") or ""
    # Replace whatever the stream produced with the canonical final answer
    # (the verifier may have swapped it for a refusal; the streaming
    # tokens won't reflect that).
    answer_msg.content = final_answer
    answer_msg.elements = _citation_elements(final_state)
    await answer_msg.update()

    # Append this turn to history. Cap at memory.max_turns * 2 (user + assistant).
    history.append({"role": "user", "content": message.content})
    history.append({"role": "assistant", "content": final_answer})
    history = history[-(settings.memory.max_turns * 2):]
    cl.user_session.set("history", history)
