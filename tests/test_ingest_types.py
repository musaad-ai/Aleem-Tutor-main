"""Sanity checks on the dataclasses shared across phase functions."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.ingest.types import Chunk, IngestResult


def test_chunk_is_frozen():
    c = Chunk(id="x__p0", text="hello", metadata={"page": 0})
    with pytest.raises(FrozenInstanceError):
        c.text = "tampered"  # type: ignore[misc]


def test_ingest_result_fields():
    r = IngestResult(
        book_id="grade8_math_sem1",
        pages_ocred=287,
        chunks_written=284,
        chroma_collection="grade_8",
    )
    assert r.pages_ocred == 287
    assert r.chunks_written == 284
    assert r.chroma_collection == "grade_8"
