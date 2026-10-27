"""Tests for chunks_from_ocr against the real captured K05 fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingest.chunk import _annotation_text, _clean_page_markdown, chunks_from_ocr

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ocr_k05_p0-2.json"


@pytest.fixture
def ocr_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_present_and_well_formed(ocr_fixture):
    assert ocr_fixture["pages"], "fixture should have pages"
    assert ocr_fixture["pages"][0]["index"] == 0
    assert any(p.get("images") for p in ocr_fixture["pages"]), \
        "fixture should include at least one annotated image"


def test_chunks_one_per_nonblank_page(ocr_fixture):
    chunks = chunks_from_ocr(
        ocr_fixture, grade=8, subject="math",
        book="test book", book_id="test_book_id",
    )
    # All 3 pages in the fixture have text — none should be skipped.
    assert len(chunks) == 3


def test_chunk_ids_are_deterministic(ocr_fixture):
    chunks = chunks_from_ocr(
        ocr_fixture, grade=8, subject="math",
        book="b", book_id="bid",
    )
    ids = [c.id for c in chunks]
    assert ids == ["bid__p0", "bid__p1", "bid__p2"]


def test_metadata_matches_chroma_schema(ocr_fixture):
    chunks = chunks_from_ocr(
        ocr_fixture, grade=8, subject="math",
        book="Math Book", book_id="bid",
    )
    md = chunks[0].metadata
    assert md["grade"] == 8
    assert md["subject"] == "math"
    assert md["book"] == "Math Book"
    assert md["book_id"] == "bid"
    assert md["page"] == 0
    assert md["chapter"] == ""
    assert md["lesson_title"] == ""
    assert md["content_type"] == "lesson_body"


def test_image_annotations_inlined(ocr_fixture):
    chunks = chunks_from_ocr(
        ocr_fixture, grade=8, subject="math",
        book="b", book_id="bid",
    )
    # Page 1 has 2 annotated QR codes; page 2 has the calligraphy image.
    page1 = next(c for c in chunks if c.metadata["page"] == 1)
    page2 = next(c for c in chunks if c.metadata["page"] == 2)
    assert "[Image: An image of a QR code.]" in page1.text
    assert "[Image: The image shows Arabic calligraphy in green color.]" in page2.text
    # No raw placeholders should leak through.
    assert "![img-" not in page1.text
    assert "![img-" not in page2.text


def test_blank_pages_are_skipped():
    ocr = {
        "pages": [
            {"index": 0, "markdown": "", "images": []},
            {"index": 1, "markdown": "   \n   ", "images": []},
            {"index": 2, "markdown": "real content", "images": []},
        ]
    }
    chunks = chunks_from_ocr(ocr, grade=8, subject="math", book="b", book_id="bid")
    assert [c.metadata["page"] for c in chunks] == [2]


def test_image_with_no_annotation_is_stripped():
    ocr = {
        "pages": [{
            "index": 0,
            "markdown": "before ![img-0.png](img-0.png) after",
            "images": [{"id": "img-0.png", "image_annotation": None}],
        }]
    }
    chunks = chunks_from_ocr(ocr, grade=8, subject="math", book="b", book_id="bid")
    assert chunks[0].text == "before  after"


def test_clean_page_markdown_handles_invalid_annotation_json():
    md = "x ![img-0](img-0) y"
    images = [{"id": "img-0", "image_annotation": "not json"}]
    assert _clean_page_markdown(md, images) == "x  y"


def test_annotation_text_extracts_description():
    assert _annotation_text('{"description": "a thing", "kind": "k"}') == "a thing"
    assert _annotation_text("{}") is None
    assert _annotation_text(None) is None
    assert _annotation_text("garbage") is None
