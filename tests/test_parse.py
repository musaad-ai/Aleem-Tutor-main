"""Tests for `parse_citations()` — the §3 structural citation parser."""

from __future__ import annotations

from src.graph.parse import parse_citations
from src.graph.state import Chunk


def _chunk(text: str, n: int) -> Chunk:
    return Chunk(
        text=text,
        grade=7,
        subject="islamic_studies",
        book="b",
        chapter="ch",
        lesson_title=f"L{n}",
        page=n,
        content_type="lesson_body",
        rerank_score=1.0,
    )


def test_inline_markers_map_to_chunks_in_order():
    chunks = [_chunk("A", 1), _chunk("B", 2), _chunk("C", 3)]
    answer = "Photosynthesis is X [1]. Chlorophyll absorbs light [2]."
    citations, flags = parse_citations(answer, chunks)
    assert [c.n for c in citations] == [1, 2]
    assert citations[0].chunk.text == "A"
    assert citations[1].chunk.text == "B"
    assert flags == []


def test_duplicate_markers_are_deduplicated_by_n():
    chunks = [_chunk("A", 1), _chunk("B", 2)]
    answer = "Plants use sunlight [1]. They also produce oxygen [1]. Glucose forms too [2]."
    citations, flags = parse_citations(answer, chunks)
    assert [c.n for c in citations] == [1, 2]
    assert flags == []


def test_out_of_range_markers_are_flagged():
    chunks = [_chunk("A", 1), _chunk("B", 2)]
    answer = "Photosynthesis [1] and respiration [5]."
    citations, flags = parse_citations(answer, chunks)
    assert [c.n for c in citations] == [1]
    assert "out_of_range:[5]" in flags


def test_no_citations_flag_when_long_answer_has_no_markers():
    chunks = [_chunk("A", 1)]
    answer = (
        "Photosynthesis is the process by which plants use sunlight, water, "
        "and carbon dioxide to make food. It happens in the chloroplasts."
    )
    citations, flags = parse_citations(answer, chunks)
    assert citations == []
    assert "no_citations" in flags


def test_short_answer_without_markers_is_not_flagged():
    chunks = [_chunk("A", 1)]
    answer = "Hello!"
    citations, flags = parse_citations(answer, chunks)
    assert citations == []
    assert flags == []


def test_brackets_with_non_numeric_content_ignored():
    chunks = [_chunk("A", 1)]
    answer = "See [Image: a leaf] for context [1]."
    citations, flags = parse_citations(answer, chunks)
    assert [c.n for c in citations] == [1]
    assert flags == []
