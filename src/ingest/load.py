"""Phase 3 of ingest: Chunks -> Jina-v4 embeddings -> Chroma collection.

Decisions implemented here:
- D10 delete-then-add keyed on `book_id`. `upsert` would silently leave
  stale chunks behind when a re-run produces fewer chunks; this avoids
  that whole class of bug.
- D10 `is not None` guard so the "no chunks" case skips the delete cleanly
  without re-introducing stale-chunk risk on an empty-string `book_id`.
- D11 batched `collection.add(...)` using
  `settings.ingestion.embed_batch_size` — the embedding step is the only
  bottleneck and the safest place to throttle for small GPUs.
"""

from __future__ import annotations

import logging

from src.config import settings
from src.retrieval.chroma_client import get_collection

from .types import Chunk

log = logging.getLogger("aleem.ingest.load")


def load_chunks(chunks: list[Chunk], *, grade: int) -> int:
    """Replace `book_id`'s chunks in the grade collection with `chunks`.

    Returns the number of chunks written (== len(chunks)). Safe to call
    with `chunks=[]` — nothing is deleted in that case (the orchestrator
    catches the empty case earlier; this is just a defensive guard).
    """
    collection = get_collection(grade)

    book_id = chunks[0].metadata["book_id"] if chunks else None
    if book_id is not None:
        collection.delete(where={"book_id": book_id})

    if not chunks:
        log.info("no chunks to load (book_id=%r)", book_id)
        return 0

    batch = settings.ingestion.embed_batch_size
    total = len(chunks)
    batches = (total + batch - 1) // batch
    for i in range(0, total, batch):
        slab = chunks[i : i + batch]
        log.info("embedding batch %d/%d (%d chunks)", i // batch + 1, batches, len(slab))
        collection.add(
            ids=[c.id for c in slab],
            documents=[c.text for c in slab],
            metadatas=[c.metadata for c in slab],
        )

    log.info("loaded %d chunks into %s", total, collection.name)
    return total
