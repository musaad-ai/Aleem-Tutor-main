"""Chroma client + collection helpers.

One persistent client rooted at `$CHROMA_DIR` (defaults to `./chroma`).
One collection per grade — `grade_4`, `grade_7`, `grade_8`, `grade_10` —
per `BUILD_SPEC.md §4.3` (grade isolation is structural, not policy-based).
Grade 8 was added alongside the OCR pipeline (OCR_implementation.md D14
update) so the first real ingest had a pilot-grade PDF on hand.

Metadata schema for every chunk added by the ingestion pipeline
(`OCR_implementation.md` D8 supersedes `BUILD_SPEC.md §4.2`):
    grade         : int        — 4 | 7 | 8 | 10
    subject       : str        — one of SUBJECTS
    book          : str        — human-readable display name (citations)
    book_id       : str        — stable short handle (e.g. grade8_math_sem1).
                                 Used by D10 `delete(where={"book_id": …})`
                                 to nuke a book's chunks on re-ingest.
    chapter       : str
    lesson_title  : str
    page          : int        — 0-indexed (Mistral OCR convention);
                                 UI renders page + 1.
    content_type  : str        — lesson_body | example | exercise | definition

Subject is a *metadata filter* applied at query time, not a separate
collection (`BUILD_SPEC.md §4.3`).
"""

from __future__ import annotations

import os
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection

from .embeddings import JinaV4EmbeddingFunction, TaskMode

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHROMA_DIR = REPO_ROOT / "chroma"

GRADES: tuple[int, ...] = (4, 7, 8, 10)
SUBJECTS: tuple[str, ...] = (
    "islamic_studies",
    "social_studies",
    "english",
    "digital_skills",
)


def chroma_dir() -> Path:
    raw = os.environ.get("CHROMA_DIR")
    if not raw:
        return DEFAULT_CHROMA_DIR
    p = Path(raw)
    # Relative paths in .env (e.g. `./chroma`) should resolve against the
    # repo root, not whatever CWD a caller happens to launch from. Without
    # this, `chainlit run` from src/ui/ would silently create a bogus
    # src/ui/chroma/ and the ingested collections would appear missing.
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p.resolve()


def get_client() -> chromadb.api.ClientAPI:
    path = chroma_dir()
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def collection_name(grade: int) -> str:
    if grade not in GRADES:
        raise ValueError(f"grade must be one of {GRADES}, got {grade!r}")
    return f"grade_{grade}"


def get_collection(grade: int, task_mode: TaskMode = "passage") -> Collection:
    """Open the persisted collection for a grade with Jina-v4 re-attached.

    Chroma persists collection data but not the embedding function instance,
    so the embedder must be supplied every time the collection is opened.
    """
    client = get_client()
    return client.get_collection(
        name=collection_name(grade),
        embedding_function=JinaV4EmbeddingFunction(task_mode=task_mode),
    )
