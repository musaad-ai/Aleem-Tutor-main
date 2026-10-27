"""Agent tools — `@tool retrieve(query)`.

docs/WORKFLOW_SANDBOX.md §3: the agent has exactly one tool, `retrieve(query)`.
It calls Chroma (top-20) → Jina reranker (top-5) and returns a string the
LLM can read directly. Rerank scores are included so the agent can judge
chunk quality and re-query or refuse (§4 "gradient signal").

Per-request context (grade, subject) and the chunk side-channel are
threaded through `contextvars.ContextVar`s so the tool's LLM-facing
signature stays narrow (`query: str`) while the surrounding agent run
still knows the student's grade and which retrieve calls fired with
which chunks. `run_agent()` in `agent.py` sets the vars at the start of
each turn and drains the recorded calls at the end.

`_STUB_CHUNKS` is preserved as a fallback for two reasons:
  - The test suite forces `backend=fake` and never ingests anything,
    so grade collections are empty during tests — falling back to stubs
    keeps the agent tests honest without mocking Chroma.
  - Useful early-error UX: if you run a query against a grade that
    hasn't been ingested yet, you get a working answer with a clear
    "stub data" rerank-score signature instead of a crash.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from langchain_core.tools import tool

from src.config import settings
from src.graph.state import Chunk, ToolCallRecord
from src.retrieval.chroma_client import get_collection
from src.retrieval.reranker import get_reranker

log = logging.getLogger("aleem.graph.tools")


# ---------------------------------------------------------------------------
# Per-request context (set by run_agent before invoking the agent loop)
# ---------------------------------------------------------------------------

_grade_var: ContextVar[int | None] = ContextVar("aleem_grade", default=None)
_subject_var: ContextVar[str | None] = ContextVar("aleem_subject", default=None)
_tool_calls_var: ContextVar[list[ToolCallRecord] | None] = ContextVar(
    "aleem_tool_calls", default=None
)


def set_request_context(
    grade: int,
    subject: str,
) -> list[ToolCallRecord]:
    """Bind grade/subject for the current async task and return a fresh
    tool-call accumulator. Call this once at the start of `run_agent()`.

    Returns the list the tool will append to so the caller can read back
    the full sequence of (query, chunks) pairs after the agent finishes.
    """
    _grade_var.set(grade)
    _subject_var.set(subject)
    bucket: list[ToolCallRecord] = []
    _tool_calls_var.set(bucket)
    return bucket


def get_recorded_tool_calls() -> list[ToolCallRecord]:
    """Snapshot of every retrieve() call made under the current context."""
    return list(_tool_calls_var.get() or [])


# ---------------------------------------------------------------------------
# Stub data (test mode + empty-collection fallback)
# ---------------------------------------------------------------------------

_STUB_CHUNKS: list[Chunk] = [
    Chunk(
        text=(
            "Photosynthesis is the process by which green plants use sunlight, "
            "water, and carbon dioxide to produce glucose and oxygen."
        ),
        grade=7,
        subject="islamic_studies",
        book="stub_book.pdf",
        chapter="Chapter 1",
        lesson_title="Stub Lesson — Photosynthesis Basics",
        page=12,
        content_type="lesson_body",
        rerank_score=0.94,
    ),
    Chunk(
        text=(
            "Chlorophyll, the green pigment in plant cells, absorbs light energy "
            "from the sun and converts it into chemical energy."
        ),
        grade=7,
        subject="islamic_studies",
        book="stub_book.pdf",
        chapter="Chapter 1",
        lesson_title="Stub Lesson — Photosynthesis Basics",
        page=13,
        content_type="lesson_body",
        rerank_score=0.81,
    ),
    Chunk(
        text=(
            "The two stages of photosynthesis are the light-dependent reactions "
            "and the Calvin cycle (light-independent reactions)."
        ),
        grade=7,
        subject="islamic_studies",
        book="stub_book.pdf",
        chapter="Chapter 1",
        lesson_title="Stub Lesson — Two Stages",
        page=15,
        content_type="definition",
    ),
]


# ---------------------------------------------------------------------------
# Chroma + reranker glue (moved from old src/graph/nodes/retrieve.py)
# ---------------------------------------------------------------------------


def _chunk_from_chroma(
    text: str,
    metadata: dict[str, Any],
    rerank_score: float,
) -> Chunk:
    return Chunk(
        text=text,
        grade=int(metadata.get("grade", 0)),
        subject=str(metadata.get("subject", "")),
        book=str(metadata.get("book", "")),
        book_id=str(metadata.get("book_id", "")),
        chapter=str(metadata.get("chapter", "")),
        lesson_title=str(metadata.get("lesson_title", "")),
        page=int(metadata.get("page", 0)),
        content_type=str(metadata.get("content_type", "lesson_body")),
        rerank_score=rerank_score,
    )


def _retrieve_real(grade: int, subject: str, question: str) -> list[Chunk]:
    col = get_collection(grade, task_mode="query")
    if col.count() == 0:
        log.warning(
            "grade_%d collection is empty — falling back to stub chunks. "
            "Run `uv run python -m src.ingest ...` to populate it.",
            grade,
        )
        return list(_STUB_CHUNKS)

    top_k_retrieve = settings.retrieval.top_k_retrieve
    top_k_rerank = settings.retrieval.top_k_rerank

    result = col.query(
        query_texts=[question],
        n_results=top_k_retrieve,
        where={"subject": subject},
    )
    documents = (result.get("documents") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]

    if not documents:
        log.warning("no chunks matched subject=%s in grade_%d", subject, grade)
        return []

    scores = get_reranker().score(question, list(documents))
    paired = list(zip(documents, metadatas, scores, strict=False))
    paired.sort(key=lambda t: t[2], reverse=True)

    return [
        _chunk_from_chroma(text=doc, metadata=md or {}, rerank_score=score)
        for doc, md, score in paired[:top_k_rerank]
    ]


# ---------------------------------------------------------------------------
# LLM-facing tool
# ---------------------------------------------------------------------------


def format_chunks_for_llm(start_index: int, chunks: list[Chunk]) -> str:
    """Render chunks as `[n] (relevance: 0.94) <text>` lines.

    `start_index` is the global citation counter so the LLM sees a single
    monotonically-increasing numbering across multiple retrieve calls
    (i.e. call 1 returns [1..5], call 2 returns [6..10]).
    """
    if not chunks:
        return "(no chunks found — try a different query or refuse)"
    lines: list[str] = []
    for offset, chunk in enumerate(chunks):
        n = start_index + offset
        lines.append(
            f"[{n}] (relevance: {chunk.rerank_score:.2f}) {chunk.text}"
        )
    return "\n\n".join(lines)


@tool
def retrieve(query: str) -> str:
    """Search the student's grade-level textbooks for relevant passages.

    Construct a query that works for semantic search: use topic terms, not
    the student's exact phrasing. Resolve pronouns and references from
    chat history first. Phrase the query in the textbook's own language
    (stated in the system prompt) so it matches the embedded chunks.

    Returns a numbered list of chunks formatted as
    `[n] (relevance: 0.94) <chunk text>`. Use the numbers as inline `[n]`
    citations in your answer. Read the relevance scores: if everything
    looks weak (e.g. all < 0.3), refine your query and call retrieve
    again, or refuse and suggest related topics from what you found.
    """
    grade = _grade_var.get()
    subject = _subject_var.get()
    if grade is None or subject is None:
        raise RuntimeError(
            "retrieve() called outside an agent request context. "
            "Call set_request_context(grade, subject) first."
        )

    chunks = _retrieve_real(grade, subject, query)

    bucket = _tool_calls_var.get()
    start_index = 1
    if bucket is not None:
        start_index = sum(len(tc.chunks) for tc in bucket) + 1
        bucket.append(ToolCallRecord(query=query, chunks=chunks))

    return format_chunks_for_llm(start_index, chunks)
