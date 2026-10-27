"""Topical verifier (docs/WORKFLOW_SANDBOX.md §4, safety layer 3).

One cheap structured-output LLM call asks: "is this answer about a topic
the chunks actually discuss?" Rejection means refuse immediately — no
retry loop (§4 "Why no retry loop on verifier rejection?").

Per-claim verification is **explicitly out of scope** (§4 "Why a topical
verifier and not a per-claim one?"). If logs show the residual
chloroplasts-vs-mitochondria failure mode is too common, upgrade to per-
claim later — but only then.

Backend=fake short-circuits with a canned `(on_topic=True, reason="...")`
because `FakeListChatModel` can't drive `with_structured_output`. Tests
that need an off-topic verdict monkeypatch `verify_topical` directly.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from pydantic import BaseModel, Field

from src.config import settings
from src.graph.client import get_verifier_llm
from src.graph.prompts import render_pair
from src.graph.state import Chunk

log = logging.getLogger("aleem.graph.verifier")


class VerifierDecision(BaseModel):
    """Structured output from the topical verifier."""

    on_topic: bool = Field(
        description=(
            "True if the answer is about the same general topic as the "
            "retrieved chunks; False if it drifts to a topic the chunks "
            "don't cover."
        )
    )
    reason: str = Field(
        description="One short sentence explaining the decision."
    )


async def verify_topical(
    question: str,
    answer: str,
    chunks: Sequence[Chunk],
) -> VerifierDecision:
    """Run the topical verifier. Returns the structured decision."""
    if not answer.strip():
        return VerifierDecision(on_topic=False, reason="empty answer")

    if settings.llm.backend == "fake":
        # Tests that want an off-topic verdict monkeypatch this function.
        return VerifierDecision(on_topic=True, reason="stub verifier (fake backend)")

    system, user = render_pair(
        "verifier.j2",
        question=question,
        answer=answer,
        chunks=list(chunks),
    )
    llm = get_verifier_llm(structured=VerifierDecision)
    try:
        return await llm.ainvoke([("system", system), ("user", user)])
    except Exception as exc:  # noqa: BLE001 — fail open, never block the answer
        # Structured output can fail when the model hits max_tokens or
        # returns malformed JSON. The spec calls for immediate refuse on
        # off_topic, but treating a verifier crash as "off_topic" would
        # punish answers for an infrastructure problem. Default to
        # on_topic and record the error in the reason field so it's
        # visible in the L18 log.
        log.warning("verifier call failed: %s — falling back to on_topic", exc)
        return VerifierDecision(
            on_topic=True,
            reason=f"verifier error (fail-open): {type(exc).__name__}",
        )
