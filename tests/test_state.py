"""State contracts — `Chunk`, `AgentState`, `initial_agent_state()`."""

from __future__ import annotations

from src.graph.state import Chunk, initial_agent_state


def test_chunk_dataclass_round_trip():
    c = Chunk(
        text="hello",
        grade=7,
        subject="islamic_studies",
        book="b.pdf",
        chapter="ch1",
        lesson_title="L1",
        page=3,
        content_type="lesson_body",
        rerank_score=0.5,
    )
    md = c.as_metadata()
    assert md["grade"] == 7
    assert md["lesson_title"] == "L1"
    assert md["page"] == 3
    assert md["rerank_score"] == 0.5


def test_initial_agent_state_defaults():
    s = initial_agent_state(grade=4, subject="arabic", user_query="hello")
    # Required fields
    assert s["grade"] == 4
    assert s["subject"] == "arabic"
    assert s["user_query"] == "hello"
    # Defaults
    assert s["history"] == []
    assert s["final_answer"] == ""
    assert s["tool_calls"] == []
    assert s["citations"] == []
    assert s["citation_flags"] == []
    assert s["refused"] is False
    assert s["refusal_reason"] == ""
    assert s["verifier_verdict"] == "skipped"
    assert s["debug"] == {}


def test_initial_agent_state_with_history():
    history = [{"role": "user", "content": "hi"}]
    s = initial_agent_state(
        grade=10, subject="math", user_query="quiz me", history=history
    )
    assert s["history"] == history
