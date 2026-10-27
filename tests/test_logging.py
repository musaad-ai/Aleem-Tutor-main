"""L18 logging — `@timed` decorator unit tests.

Full agent-path JSONL coverage lives in `tests/test_logging_agent.py`.
"""

from __future__ import annotations

from src.graph.logging import timed


async def test_timed_adds_latency_ms_key():
    @timed("dummy")
    async def node(state):
        return {"answer": "x"}

    out = await node({})
    assert "latency_ms" in out["debug"]
    assert "dummy" in out["debug"]["latency_ms"]
    assert out["debug"]["latency_ms"]["dummy"] >= 0
    assert out["answer"] == "x"


async def test_timed_merges_with_existing_debug():
    @timed("alpha")
    async def node(state):
        return {"debug": {"upstream_key": 42, "latency_ms": {"earlier": 5}}}

    out = await node({"debug": {"older_key": "preserved"}})
    # Existing keys preserved
    assert out["debug"]["older_key"] == "preserved"
    assert out["debug"]["upstream_key"] == 42
    # Latency map merged, not overwritten
    assert out["debug"]["latency_ms"]["earlier"] == 5
    assert out["debug"]["latency_ms"]["alpha"] >= 0
