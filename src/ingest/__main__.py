"""CLI entry point for the ingest pipeline.

    uv run python -m src.ingest <pdf> \\
        --grade {4,7,8,10} \\
        --subject {arabic,islamic_studies,social_studies,english,math} \\
        --book "Display Name" \\
        --book-id stable_short_handle \\
        [--pages 0-10]

Prints the `IngestResult` as JSON on stdout. Stage-by-stage progress goes
to stderr via the standard logging setup (D17). Library code never calls
`basicConfig`; only this entry point does.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict

from src.retrieval.chroma_client import GRADES, SUBJECTS

from . import ingest_book


def _parse_pages(spec: str | None) -> list[int] | None:
    if spec is None:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.ingest",
        description="OCR + chunk + embed a textbook PDF into the per-grade Chroma collection.",
    )
    parser.add_argument("pdf_path", help="Path to the textbook PDF.")
    parser.add_argument(
        "--grade", type=int, required=True, choices=list(GRADES),
        help="Pilot grade.",
    )
    parser.add_argument(
        "--subject", required=True, choices=list(SUBJECTS),
        help="Subject — used as a metadata filter at query time.",
    )
    parser.add_argument("--book", required=True, help="Human-readable display name.")
    parser.add_argument(
        "--book-id", required=True,
        help="Stable short handle, e.g. grade8_math_sem1. Used for delete-by-book on re-ingest.",
    )
    parser.add_argument(
        "--pages", default=None,
        help="Partial-book pages (D16) — e.g. '0-10' or '0,2,4'. Omit for full book.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    result = ingest_book(
        args.pdf_path,
        grade=args.grade,
        subject=args.subject,
        book=args.book,
        book_id=args.book_id,
        pages=_parse_pages(args.pages),
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
