"""Dataclasses shared across the ingest phase functions.

`Chunk` mirrors `src/graph/state.py::Chunk` for the *write* side. The
graph's Chunk also carries a `rerank_score` (filled by the reranker at
query time); the ingest Chunk doesn't, since reranking happens later.
The metadata dict produced here lands in Chroma exactly as-is, so its
shape must match `src/retrieval/chroma_client.py:8-26`.

`IngestResult` is the orchestrator return value — what the CLI prints.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IngestResult:
    book_id: str
    pages_ocred: int
    chunks_written: int
    chroma_collection: str
