"""Jina-v4 embedding function for ChromaDB.

Wraps `jinaai/jina-embeddings-v4` (HuggingFace) in Chroma's `EmbeddingFunction`
protocol so collections can call it transparently from `collection.add(...)`
and `collection.query(...)`.

The HF model is held as a **module-level singleton** under a lock, not on
the instance. `chroma_client.get_collection` builds a fresh
`JinaV4EmbeddingFunction` every time it's called (Chroma persists data
but not the embedder), so per-instance caching would mean re-loading
~3GB of weights on every retrieve. Sharing one model across instances
also lets `prewarm.py` warm the embedder once at app boot.

Task modes (Jina-v4 supports several; we use only `retrieval.passage` for
indexing and `retrieval.query` for query-time embedding):
    - "passage" → for documents being added to the index.
    - "query"   → for user queries at retrieval time.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Literal

from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

log = logging.getLogger("aleem.retrieval.embeddings")

JINA_MODEL_ID = "jinaai/jina-embeddings-v4"
TaskMode = Literal["passage", "query"]


_model_lock = threading.Lock()
_model: Any | None = None
_device: str | None = None


def _load_model() -> tuple[Any, str]:
    """Load Jina-v4 once and return the shared model + device.

    Double-checked locking so concurrent first-callers don't race two
    parallel `from_pretrained` downloads.
    """
    global _model, _device
    if _model is not None:
        return _model, _device  # type: ignore[return-value]
    with _model_lock:
        if _model is not None:
            return _model, _device  # type: ignore[return-value]

        import torch
        from transformers import AutoModel

        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

        token = os.environ.get("HF_TOKEN") or None
        log.info("loading embedder %s on %s", JINA_MODEL_ID, device)
        model = AutoModel.from_pretrained(
            JINA_MODEL_ID,
            trust_remote_code=True,
            token=token,
        )
        model.to(device)
        model.eval()

        _model = model
        _device = device
        return model, device


def prewarm_embedder() -> None:
    """Force-load the embedder model. Safe to call from a background
    thread at app startup so the first retrieve doesn't pay the cold
    ~7s load tax on the asyncio loop.
    """
    _load_model()


class JinaV4EmbeddingFunction(EmbeddingFunction[Documents]):
    """Chroma-compatible embedder backed by jina-embeddings-v4.

    Instances are cheap; the underlying HF model is shared across every
    instance via the module-level singleton in `_load_model()`.
    """

    def __init__(self, task_mode: TaskMode = "passage") -> None:
        self.task_mode: TaskMode = task_mode

    @staticmethod
    def name() -> str:
        # Stable identifier Chroma stores in collection metadata so it can
        # warn if a future caller tries to attach a different embedder to
        # the same collection.
        return "jina-v4"

    def __call__(self, input: Documents) -> Embeddings:
        model, _ = _load_model()
        texts = list(input)
        if not texts:
            return []

        # Jina-v4 exposes encode_text(texts, task=..., prompt_name=...).
        # `task="retrieval"` + prompt_name "passage"/"query" selects the
        # appropriate adapter for asymmetric retrieval.
        embeddings = model.encode_text(
            texts=texts,
            task="retrieval",
            prompt_name=self.task_mode,
        )

        # encode_text may return a torch.Tensor or list[Tensor]; normalise.
        if hasattr(embeddings, "tolist"):
            return embeddings.tolist()
        return [e.tolist() for e in embeddings]
