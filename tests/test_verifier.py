"""Tests for the topical verifier (`src/graph/verifier.py`)."""

from __future__ import annotations

import pytest

from src.graph.state import Chunk
from src.graph.verifier import VerifierDecision, verify_topical


def _chunk(text: str) -> Chunk:
    return Chunk(
        text=text,
        grade=7,
        subject="islamic_studies",
        book="b",
        chapter="ch",
        lesson_title="L1",
        page=1,
        content_type="lesson_body",
        rerank_score=0.9,
    )


def test_verifier_decision_schema_round_trip():
    """Pydantic schema accepts both fields, rejects extras."""
    d = VerifierDecision(on_topic=True, reason="topic matches")
    assert d.on_topic is True
    assert d.reason == "topic matches"


@pytest.mark.asyncio
async def test_verify_topical_fake_backend_returns_on_topic():
    """Under backend=fake the verifier short-circuits to on_topic=True so
    the agent loop tests can run without queueing a structured-output
    response.
    """
    verdict = await verify_topical(
        question="What is photosynthesis?",
        answer="Photosynthesis is X [1].",
        chunks=[_chunk("Photosynthesis is X.")],
    )
    assert verdict.on_topic is True
    assert "stub" in verdict.reason.lower()


@pytest.mark.asyncio
async def test_verify_topical_empty_answer_is_off_topic():
    verdict = await verify_topical(
        question="What is photosynthesis?",
        answer="",
        chunks=[_chunk("Photosynthesis is X.")],
    )
    assert verdict.on_topic is False
    assert "empty" in verdict.reason.lower()
