"""Ceiling-hit refusal test (docs/WORKFLOW_SANDBOX.md §3).

If the agent uses up its `max_tool_calls` budget without producing a
final answer, `run_agent` catches `GraphRecursionError` and emits the
§3 refusal shape: agent's voice + topic suggestions from chunks that
*were* retrieved.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage

from src.graph.agent import run_agent
from tests.test_agent import FakeToolCallingModel


def _tool_call_message(query: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "retrieve",
                "args": {"query": query},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


@pytest.mark.asyncio
async def test_agent_hits_ceiling_and_refuses_with_topic_suggestions(
    make_agent_state, monkeypatch
):
    """Force `max_tool_calls = 1` so even two tool calls trigger the
    recursion limit. Verify the refusal carries lesson titles drawn from
    the chunks that did come back from retrieve.
    """
    from src.graph import agent as agent_mod

    fake_settings = replace(
        agent_mod.settings,
        agent=replace(agent_mod.settings.agent, max_tool_calls=1),
    )
    monkeypatch.setattr("src.graph.agent.settings", fake_settings)

    # Queue a model that keeps wanting to call retrieve forever. With
    # max_tool_calls=1 → recursion_limit=5; after the first tool turn
    # the loop must end. We queue several tool-call messages just to be
    # sure we never produce a final answer.
    fake = FakeToolCallingModel(
        responses=[
            _tool_call_message("photosynthesis", "call_1"),
            _tool_call_message("chlorophyll", "call_2"),
            _tool_call_message("calvin cycle", "call_3"),
            _tool_call_message("light reactions", "call_4"),
            AIMessage(content="Should not get here."),
        ]
    )

    state = make_agent_state(user_query="explain photosynthesis")
    result = await run_agent(state, llm=fake)

    assert result["refused"] is True
    assert result["refusal_reason"] == "ceiling_hit"
    assert result["verifier_verdict"] == "skipped"

    # The refusal cites lesson titles drawn from already-retrieved stub
    # chunks (Stub Lesson — Photosynthesis Basics / — Two Stages).
    assert "Stub Lesson" in result["final_answer"]


@pytest.mark.asyncio
async def test_ceiling_refusal_without_chunks_uses_generic_fallback(
    make_agent_state, monkeypatch
):
    """If the agent ran out of budget without any retrieve calls landing
    chunks, the refusal falls back to a generic phrasing.
    """
    from src.graph import agent as agent_mod
    from src.graph import tools as tools_mod

    fake_settings = replace(
        agent_mod.settings,
        agent=replace(agent_mod.settings.agent, max_tool_calls=1),
    )
    monkeypatch.setattr("src.graph.agent.settings", fake_settings)

    # Patch _retrieve_real to return no chunks (simulating a real empty
    # subject filter). The stub fallback won't fire because the
    # collection-count check is in _retrieve_real itself.
    monkeypatch.setattr(tools_mod, "_retrieve_real", lambda g, s, q: [])

    fake = FakeMessagesListChatModel.__bases__  # noqa: F841 — readability anchor
    fake = FakeToolCallingModel(
        responses=[
            _tool_call_message("xyz", "c1"),
            _tool_call_message("abc", "c2"),
            _tool_call_message("def", "c3"),
            _tool_call_message("ghi", "c4"),
        ]
    )

    state = make_agent_state(user_query="something obscure")
    result = await run_agent(state, llm=fake)

    assert result["refused"] is True
    assert result["refusal_reason"] == "ceiling_hit"
    assert "don't have textbook material" in result["final_answer"]
