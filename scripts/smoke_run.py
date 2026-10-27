"""Programmatic smoke test of the workflow-sandbox agent — no Chainlit, no UI.

With `llm.backend: fake` in `config.yaml`, this runs the full agent end-
to-end without an API key (the @tool short-circuits to stub chunks when
Chroma is empty). Prints the answer, the tool-call trace, the verifier
verdict, and the latency breakdown.

Usage (from repo root):

    uv run python scripts/smoke_run.py "what is photosynthesis?"
    uv run python scripts/smoke_run.py "explain X and quiz me" --grade 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Make `src.*` importable when running this script directly from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.graph.agent import run_agent  # noqa: E402
from src.graph.state import initial_agent_state  # noqa: E402


def _jsonable(obj):  # noqa: ANN001, ANN202
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


async def main(question: str, grade: int, subject: str) -> int:
    initial = initial_agent_state(
        grade=grade,
        subject=subject,
        user_query=question,
    )
    final = await run_agent(initial)

    print("\n=== FINAL ANSWER ===\n")
    print(final.get("final_answer", "(no answer)"))

    print("\n=== TOOL CALLS ===\n")
    tool_calls = final.get("tool_calls") or []
    if not tool_calls:
        print("(none)")
    for i, tc in enumerate(tool_calls, start=1):
        scores = ", ".join(f"{c.rerank_score:.2f}" for c in tc.chunks)
        print(f"{i}. retrieve({tc.query!r}) → {len(tc.chunks)} chunks [{scores}]")

    print("\n=== VERIFIER ===\n")
    print(f"verdict : {final.get('verifier_verdict')}")
    print(f"reason  : {final.get('verifier_reason')}")

    if final.get("refused"):
        print(f"\nrefused : True (reason: {final.get('refusal_reason')!r})")

    if flags := final.get("citation_flags"):
        print("\n=== CITATION FLAGS ===\n")
        for flag in flags:
            print(f"- {flag}")

    debug = final.get("debug") or {}
    if debug:
        print("\n=== DEBUG ===\n")
        print(json.dumps(_jsonable(debug), indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("question", help="The student's question.")
    p.add_argument("--grade", type=int, default=7, choices=[4, 7, 8, 10])
    p.add_argument("--subject", default="islamic_studies")
    args = p.parse_args()
    sys.exit(asyncio.run(main(args.question, args.grade, args.subject)))
