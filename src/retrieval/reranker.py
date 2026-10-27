"""Jina Reranker v2 — cross-encoder for retrieval rerank.

Same lazy-load pattern as `embeddings.py`: instantiating the class costs
nothing; the model is loaded on the first `rerank(...)` call. Singletons
across the process so the model isn't reloaded per request.

Used by `src/graph/tools.py` (the agent's `retrieve` tool) to rescore
Chroma's top-K against the query before returning the final top-N to
the agent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from src.config import settings

log = logging.getLogger("aleem.retrieval.reranker")


class JinaReranker:
    """Cross-encoder reranker for (query, passage) pairs."""

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or settings.retrieval.reranker_model
        self._model: Any | None = None
        self._device: str | None = None

    def _load(self) -> None:
        if self._model is not None:
            return

        import torch
        from transformers import AutoModelForSequenceClassification

        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

        token = os.environ.get("HF_TOKEN") or None
        log.info("loading reranker %s on %s", self.model_id, device)
        model = AutoModelForSequenceClassification.from_pretrained(
            self.model_id,
            trust_remote_code=True,
            token=token,
        )
        model.to(device)
        model.eval()

        self._model = model
        self._device = device

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Return one relevance score per passage. Higher = more relevant."""
        if not passages:
            return []
        self._load()
        pairs = [[query, p] for p in passages]
        # jina-reranker-v2 exposes compute_score(pairs, max_length=...).
        scores = self._model.compute_score(pairs, max_length=1024)  # type: ignore[union-attr]
        # Returns list[float] or a torch tensor; normalise.
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        return [float(s) for s in scores]


_singleton: JinaReranker | None = None


def get_reranker() -> JinaReranker:
    global _singleton
    if _singleton is None:
        _singleton = JinaReranker()
    return _singleton


def prewarm_reranker() -> None:
    """Force-load the reranker model. Safe to call from a background
    thread at app startup so the first retrieve doesn't pay the cold
    ~7s load tax on the asyncio loop.
    """
    get_reranker()._load()
