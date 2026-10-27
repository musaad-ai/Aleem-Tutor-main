"""Tests for the `@tool retrieve()` wrapper (docs/WORKFLOW_SANDBOX.md §3)."""

from __future__ import annotations

import pytest

from src.graph.state import Chunk
from src.graph.tools import (
    _STUB_CHUNKS,
    format_chunks_for_llm,
    get_recorded_tool_calls,
    retrieve,
    set_request_context,
)


def test_format_chunks_includes_relevance_scores():
    chunks = [
        Chunk(text="A", grade=7, subject="x", book="", chapter="",
              lesson_title="L1", page=1, content_type="lesson_body",
              rerank_score=0.94),
        Chunk(text="B", grade=7, subject="x", book="", chapter="",
              lesson_title="L1", page=2, content_type="lesson_body",
              rerank_score=0.81),
    ]
    out = format_chunks_for_llm(1, chunks)
    assert "[1] (relevance: 0.94) A" in out
    assert "[2] (relevance: 0.81) B" in out


def test_format_chunks_continues_global_numbering():
    chunks = [
        Chunk(text="C", grade=7, subject="x", book="", chapter="",
              lesson_title="L2", page=3, content_type="lesson_body",
              rerank_score=0.70),
    ]
    out = format_chunks_for_llm(start_index=6, chunks=chunks)
    assert out.startswith("[6] (relevance: 0.70) C")


def test_format_chunks_empty_returns_friendly_string():
    out = format_chunks_for_llm(1, [])
    assert "no chunks" in out.lower()


@pytest.mark.asyncio
async def test_retrieve_returns_stub_chunks_when_chroma_empty():
    """When Chroma is empty (the test default), retrieve falls back to
    `_STUB_CHUNKS` so the agent loop runs end-to-end without ingestion.
    """
    set_request_context(grade=7, subject="islamic_studies")
    formatted = await retrieve.ainvoke({"query": "photosynthesis"})
    assert "[1]" in formatted
    assert "relevance:" in formatted
    # Stub chunks are recorded in the per-request bucket.
    calls = get_recorded_tool_calls()
    assert len(calls) == 1
    assert calls[0].query == "photosynthesis"
    assert len(calls[0].chunks) == len(_STUB_CHUNKS)


@pytest.mark.asyncio
async def test_retrieve_numbers_across_multiple_calls():
    """Two retrieve calls produce chunk numbers [1..N] then [N+1..M]."""
    set_request_context(grade=7, subject="islamic_studies")
    first = await retrieve.ainvoke({"query": "photosynthesis"})
    second = await retrieve.ainvoke({"query": "chlorophyll"})

    assert "[1]" in first
    # Second call starts numbering after the first call's chunks.
    expected_start = f"[{len(_STUB_CHUNKS) + 1}]"
    assert expected_start in second


@pytest.mark.asyncio
async def test_retrieve_without_context_raises():
    """Direct retrieve() outside an agent request is a programming error."""
    # Reset the context var by binding a fresh request and then poking
    # an uninitialised one.
    from src.graph import tools as tools_mod

    # Re-bind to None to simulate "no active request"
    tools_mod._grade_var.set(None)
    tools_mod._subject_var.set(None)

    with pytest.raises(RuntimeError, match="agent request context"):
        await retrieve.ainvoke({"query": "x"})
