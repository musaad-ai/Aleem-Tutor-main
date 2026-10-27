"""Graph state types — workflow-sandbox shape.

Replaces the two-graph TaskState / OuterState pair with a single
`AgentState` that flows through `run_agent()` (`src/graph/agent.py`).
Spec: docs/WORKFLOW_SANDBOX.md §3 + §8.

`Chunk` is preserved unchanged — it still mirrors the Chroma metadata
contract in `src/retrieval/chroma_client.py:8-26` (OCR_implementation.md
D8). `Citation` and `HistoryTurn` also stay. The `Intent` literal is
gone — no intent classifier in the new pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

Language = Literal["ar", "en"]


@dataclass(frozen=True)
class Chunk:
    """A retrieved + reranked textbook chunk.

    Matches the Chroma metadata schema documented at
    `src/retrieval/chroma_client.py:8-26`. `text` is the document content;
    everything else is metadata.
    """

    text: str
    grade: int
    subject: str
    book: str
    chapter: str
    lesson_title: str
    page: int
    content_type: str  # lesson_body | example | exercise | definition
    book_id: str = ""  # stable short handle; D10 delete-by-book_id key
    rerank_score: float = 0.0

    def as_metadata(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "subject": self.subject,
            "book": self.book,
            "book_id": self.book_id,
            "chapter": self.chapter,
            "lesson_title": self.lesson_title,
            "page": self.page,
            "content_type": self.content_type,
            "rerank_score": self.rerank_score,
        }


@dataclass(frozen=True)
class Citation:
    """A single rendered citation: marker index → chunk."""

    n: int
    chunk: Chunk


class HistoryTurn(TypedDict):
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ToolCallRecord:
    """One retrieve() invocation the agent made during a turn.

    `query` is the verbatim string the agent passed to retrieve — surfaced
    in the UI ("Searching: …") and in the L18 log so demo-time and post-hoc
    review both show what the agent actually searched for.
    """

    query: str
    chunks: list[Chunk]


class AgentState(TypedDict, total=False):
    """Single state object for the workflow-sandbox agent.

    Inputs come from the UI (or smoke_run); the rest is filled by
    `run_agent()` in `src/graph/agent.py`.
    """

    # Inputs
    grade: int
    subject: str
    user_query: str
    history: list[HistoryTurn]

    # Filled by the agent loop
    final_answer: str
    tool_calls: list[ToolCallRecord]
    citations: list[Citation]
    citation_flags: list[str]
    refused: bool
    refusal_reason: str  # "off_topic" | "ceiling_hit" | "" when not refused

    # Filled by the verifier (docs/WORKFLOW_SANDBOX.md §4)
    verifier_verdict: Literal["on_topic", "off_topic", "skipped"]
    verifier_reason: str

    # Observability (L18 — dumped to JSONL at end)
    debug: dict[str, Any]


def initial_agent_state(
    grade: int,
    subject: str,
    user_query: str,
    history: list[HistoryTurn] | None = None,
) -> AgentState:
    return AgentState(
        grade=grade,
        subject=subject,
        user_query=user_query,
        history=history or [],
        final_answer="",
        tool_calls=[],
        citations=[],
        citation_flags=[],
        refused=False,
        refusal_reason="",
        verifier_verdict="skipped",
        verifier_reason="",
        debug={},
    )
