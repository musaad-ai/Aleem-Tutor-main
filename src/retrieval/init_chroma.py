"""One-time setup: create the per-grade Chroma collections.

Run from the repo root:

    python -m src.retrieval.init_chroma

Idempotent — re-running on an existing store leaves the collections
untouched and just reports their current size.

Each collection is created with the Jina-v4 embedding function attached so
that the ingestion pipeline (next task) can call `collection.add(...)`
without explicitly passing an embedder. The Jina model itself is *not*
downloaded by this script — `JinaV4EmbeddingFunction` loads lazily on first
real embedding call, and this script never adds any documents.
"""

from __future__ import annotations

import logging
import sys

from .chroma_client import GRADES, chroma_dir, collection_name, get_client
from .embeddings import JinaV4EmbeddingFunction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("init_chroma")


def main() -> int:
    client = get_client()
    embed_fn = JinaV4EmbeddingFunction(task_mode="passage")

    log.info("chroma dir: %s", chroma_dir())

    existing = {c.name for c in client.list_collections()}

    for grade in GRADES:
        name = collection_name(grade)
        was_present = name in existing

        collection = client.get_or_create_collection(
            name=name,
            embedding_function=embed_fn,
            metadata={"hnsw:space": "cosine", "grade": grade},
        )

        log.info(
            "%-9s  %s  (count=%d)",
            name,
            "exists " if was_present else "created",
            collection.count(),
        )

    log.info("done — %d collections ready", len(GRADES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
