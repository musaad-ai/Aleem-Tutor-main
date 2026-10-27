"""Background prewarm for the Jina embedder + reranker.

The first `retrieve()` call after server boot blocks the asyncio event
loop for ~15s while the two Jina models load from the HF cache. While
that runs Chainlit can't flush the next `on_chain_start` event to the
websocket, so the status bar appears frozen.

`prewarm_models_in_background()` runs both loads up front in a daemon
thread so the first query pays no cold-load tax. Skipped automatically
when no grade collection has any chunks — in that case
`tools.retrieve` returns stub chunks without touching either model
(test mode / fake backend on a fresh clone), and loading ~3GB of
weights would be wasted.
"""

from __future__ import annotations

import logging
import threading

from src.retrieval.chroma_client import GRADES, get_collection
from src.retrieval.embeddings import prewarm_embedder
from src.retrieval.reranker import prewarm_reranker

log = logging.getLogger("aleem.retrieval.prewarm")


def _any_grade_populated() -> bool:
    for grade in GRADES:
        try:
            if get_collection(grade, task_mode="query").count() > 0:
                return True
        except Exception:  # noqa: BLE001 — missing collection is not fatal
            continue
    return False


def prewarm_models() -> None:
    """Synchronous prewarm — returns when both models are in memory."""
    if not _any_grade_populated():
        log.info(
            "prewarm skipped — no grade collection has chunks "
            "(retrieve will return stubs)"
        )
        return
    log.info("prewarm: loading embedder + reranker")
    try:
        prewarm_embedder()
        prewarm_reranker()
        log.info("prewarm complete")
    except Exception as exc:  # noqa: BLE001 — never block app boot on prewarm
        log.warning("prewarm failed (continuing without): %s", exc)


def prewarm_models_in_background() -> threading.Thread:
    """Kick off prewarm in a daemon thread and return it.

    Daemon so it doesn't keep the process alive if Chainlit exits mid-load.
    """
    t = threading.Thread(
        target=prewarm_models,
        name="aleem-prewarm",
        daemon=True,
    )
    t.start()
    return t
