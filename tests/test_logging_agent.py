"""Tests for the workflow-sandbox JSONL log schema (`src/graph/logging.py`)."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from src.graph.agent import run_agent
from src.graph.logging import LOGS_DIR
from tests.test_agent import FakeToolCallingModel


@pytest.mark.asyncio
async def test_agent_run_writes_new_jsonl_record(make_agent_state, tmp_path, monkeypatch):
    """One agent run → one JSONL line with the new schema."""
    log_dir = tmp_path / "logs"
    monkeypatch.setattr("src.graph.logging.LOGS_DIR", log_dir)

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
            AIMessage(content="Photosynthesis is X [1]. Chlorophyll absorbs light [2]."),
        ]
    )

    state = make_agent_state()
    await run_agent(state, llm=fake)

    files = list(log_dir.glob("queries-*.jsonl"))
    assert len(files) == 1, f"expected one log file, got {files}"
    lines = files[0].read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])

    # New-schema fields are present
    for key in (
        "ts", "session_id", "query", "grade", "subject",
        "tool_calls", "answer_length", "citations_count",
        "citation_flags", "verifier_verdict", "verifier_reason",
        "refused", "refusal_reason", "latency_ms",
    ):
        assert key in record, f"missing field: {key}"

    # Old-schema fields are gone
    for old in ("intent", "num_tasks", "self_check_passed",
                "self_check_verdict", "standalone_question"):
        assert old not in record, f"unexpected legacy field: {old}"

    # Per-call detail captured
    assert len(record["tool_calls"]) == 1
    assert record["tool_calls"][0]["query"] == "photosynthesis"
    assert record["tool_calls"][0]["num_chunks"] >= 1
    assert isinstance(record["tool_calls"][0]["rerank_scores"], list)

    assert record["refused"] is False
    assert record["citations_count"] == 2
    assert record["verifier_verdict"] == "on_topic"
    assert "agent_loop" in record["latency_ms"]
    assert "total" in record["latency_ms"]


def test_logs_dir_is_repo_relative():
    """Sanity: the production log dir lives under the repo (gitignored)."""
    assert LOGS_DIR.name == "logs"
