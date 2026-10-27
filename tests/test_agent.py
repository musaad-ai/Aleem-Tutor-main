"""End-to-end test for the workflow-sandbox agent.

Uses `FakeMessagesListChatModel` (queued with hand-crafted AIMessages) so
the `create_react_agent` loop is actually exercised — tool call, tool
response, final answer. The fake model overrides `bind_tools` as a
passthrough since LangGraph's prebuilt agent invokes it during compile.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage

from src.graph.agent import run_agent


class FakeToolCallingModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel + passthrough `bind_tools`.

    LangGraph's create_react_agent calls `model.bind_tools(tools)` at
    compile time. The base class raises NotImplementedError; here we
    return self so the queued AIMessages drive the loop unchanged.
    """

    def bind_tools(
        self,
        tools: Any,  # noqa: ARG002 — tool schemas are unused in fake mode
        *,
        tool_choice: str | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> FakeToolCallingModel:
        return self


@pytest.mark.asyncio
async def test_agent_runs_tool_call_then_produces_cited_answer(make_agent_state):
    """Agent calls retrieve() once, then writes a cited final answer."""
    fake = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "retrieve",
                        "args": {"query": "photosynthesis"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content=(
                    "Photosynthesis is the process plants use to make food "
                    "from sunlight, water, and carbon dioxide [1]. Chlorophyll "
                    "absorbs the light [2]."
                ),
            ),
        ]
    )

    state = make_agent_state()
    result = await run_agent(state, llm=fake)

    assert result["refused"] is False
    assert "Photosynthesis" in result["final_answer"]
    # Two retrieve-numbered citations in the answer → two Citation objects.
    cit_ns = [c.n for c in result["citations"]]
    assert cit_ns == [1, 2]
    # One tool call was recorded with the verbatim query string.
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0].query == "photosynthesis"
    # Verifier ran under fake-backend short-circuit and approved.
    assert result["verifier_verdict"] == "on_topic"


@pytest.mark.asyncio
async def test_agent_records_history_into_message_stream(make_agent_state):
    """Prior turns are injected as Human/AI messages before the new query."""
    fake = FakeToolCallingModel(
        responses=[
            AIMessage(content="Hi! What would you like to learn about?"),
        ]
    )

    state = make_agent_state(
        user_query="Hello",
        history=[
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! How can I help?"},
        ],
    )
    result = await run_agent(state, llm=fake)

    assert "What would you like to learn" in result["final_answer"]
    # No tool calls needed for a greeting.
    assert result["tool_calls"] == []
    # No chunks ⇒ verifier skipped.
    assert result["verifier_verdict"] == "skipped"


@pytest.mark.asyncio
async def test_agent_records_latency_in_debug(make_agent_state):
    fake = FakeToolCallingModel(
        responses=[AIMessage(content="Quick reply.")]
    )
    state = make_agent_state(user_query="hi")
    result = await run_agent(state, llm=fake)

    latency = result["debug"]["latency_ms"]
    assert "agent_loop" in latency
    assert "total" in latency
    assert latency["total"] >= latency["agent_loop"]
