"""Aleem ingest pipeline — PDF -> OCR -> chunk -> embed -> Chroma.

Public entry point is `ingest_book(...)`. The phase functions
(`ocr_book`, `chunks_from_ocr`, `load_chunks`) are exported too for the
probe script and for tests that want to drive a single phase in
isolation.

Design lives in `OCR_implementation.md` — read it before changing any
behavior here.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.retrieval.chroma_client import GRADES, SUBJECTS, collection_name, get_collection

from .chunk import chunks_from_ocr
from .load import load_chunks
from .ocr import ocr_book
from .types import Chunk, IngestResult

__all__ = [
    "ingest_book",
    "ocr_book",
    "chunks_from_ocr",
    "load_chunks",
    "Chunk",
    "IngestResult",
]

log = logging.getLogger("aleem.ingest")


def _cache_subdir(book_id: str, pages: list[int] | None) -> str:
    """D16: partial-page runs get a `+pages{lo}-{hi}` sibling cache dir."""
    if not pages:
        return book_id
    lo, hi = min(pages), max(pages)
    return f"{book_id}+pages{lo}-{hi}"


def ingest_book(
    pdf_path: str | Path,
    *,
    grade: int,
    subject: str,
    book: str,
    book_id: str,
    pages: list[int] | None = None,
) -> IngestResult:
    """Orchestrator — runs OCR, chunking, and Chroma load in sequence.

    Validates inputs up-front (D13) so we never burn a paid Mistral call
    on a config error. `pages` is the dev-iteration knob from D16; leave
    it None for a full-book run.
    """
    if not book_id:
        raise ValueError("book_id must be a non-empty string")
    if subject not in SUBJECTS:
        raise ValueError(f"subject must be one of {SUBJECTS}, got {subject!r}")
    if grade not in GRADES:
        raise ValueError(f"grade must be one of {GRADES}, got {grade!r}")

    try:
        get_collection(grade)
    except Exception as e:
        raise ValueError(
            f"No Chroma collection for grade {grade}. "
            f"Run: uv run python -m src.retrieval.init_chroma"
        ) from e

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    cache_dir = (
        Path("data/processed") / f"grade_{grade}" / subject / _cache_subdir(book_id, pages)
    )
    log.info("ingest_book: book_id=%s grade=%d subject=%s cache=%s",
             book_id, grade, subject, cache_dir)

    ocr = ocr_book(pdf_path, cache_dir=cache_dir, pages=pages)
    chunks = chunks_from_ocr(
        ocr, grade=grade, subject=subject, book=book, book_id=book_id
    )
    written = load_chunks(chunks, grade=grade)

    return IngestResult(
        book_id=book_id,
        pages_ocred=len(ocr.get("pages", [])),
        chunks_written=written,
        chroma_collection=collection_name(grade),
    )
