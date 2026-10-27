"""Citation parse + structural flag detection (docs/WORKFLOW_SANDBOX.md §3, layer 2).

`parse_citations(answer, chunks)` walks the model's output for `[n]`
markers, maps them to retrieved chunks, and flags structural problems:

  - `out_of_range:[n]`      — the model cited a number with no
                              corresponding chunk in the agent's accumulated
                              retrieve calls.
  - `no_citations`          — the answer is long enough to be a factual
                              response but contains zero `[n]` markers.

This is the free structural check from §3 — it runs on every answer
that isn't a refusal. The topical verifier (`verifier.py`) is the LLM-
backed layer that catches catastrophic topic drift; this layer catches
the cheap stuff (missing brackets, hallucinated chunk numbers).

Pure function — no LLM, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.graph.state import Chunk, Citation

# Match `[1]`, `[12]`, etc. Avoids `[]` and non-numeric brackets like
# `[Image: …]`. Multi-digit is fine (the agent's global citation counter
# can exceed 9 across multiple retrieve calls).
_CITATION_RE = re.compile(r"\[(\d+)\]")

# Below this length we treat the answer as too short to require citations
# (greetings, single-sentence refusals, etc.). Tuned by feel; revisit if
# the JSONL log shows false positives.
_FACTUAL_ANSWER_MIN_CHARS = 80


def _extract_marker_indices(answer: str) -> list[int]:
    """Return [n] marker integers in the order they appear in `answer`."""
    return [int(m.group(1)) for m in _CITATION_RE.finditer(answer)]


def parse_citations(
    answer: str,
    chunks: Sequence[Chunk],
) -> tuple[list[Citation], list[str]]:
    """Parse `[n]` markers and produce structured citations + flags.

    Args:
        answer: The agent's final answer text.
        chunks: Every chunk the agent retrieved this turn, in global order
            (i.e. chunk index `i` corresponds to marker `[i+1]`).

    Returns:
        `(citations, flags)`. `citations` is deduped by marker number and
        ordered by first appearance in the answer. `flags` is a list of
        short strings — see module docstring for the vocabulary.
    """
    markers = _extract_marker_indices(answer)
    flags: list[str] = []
    citations: list[Citation] = []
    seen: set[int] = set()

    for n in markers:
        if n in seen:
            continue
        seen.add(n)
        if 1 <= n <= len(chunks):
            citations.append(Citation(n=n, chunk=chunks[n - 1]))
        else:
            flags.append(f"out_of_range:[{n}]")

    if not markers and len(answer.strip()) >= _FACTUAL_ANSWER_MIN_CHARS:
        flags.append("no_citations")

    return citations, flags
