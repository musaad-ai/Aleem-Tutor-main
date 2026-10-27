"""OCR-only probe — no chunking, no Chroma writes.

Cheap dev tool: feed a PDF + page range, get `raw_ocr.json` (and per-page
markdown dumps via D15) for inspection. Used to answer the four open
questions in `OCR_implementation.md` §Validation plan before committing
to the chunker design.

    uv run python scripts/ocr_probe.py path/to/book.pdf --pages 0-2 \\
        --out data/processed/_probe/
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingest.ocr import ocr_book  # noqa: E402


def _parse_pages(spec: str) -> list[int]:
    """Parse '0-2' or '0,2,4' or '0-2,5' into a sorted list of ints."""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument(
        "--pages",
        default="0-2",
        help="Page range, 0-indexed. e.g. '0-2' (default) or '0,1,2' or '0-5,7'.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/processed/_probe"),
        help="Cache dir for raw_ocr.json + per-page markdown.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    pages = _parse_pages(args.pages)
    ocr = ocr_book(args.pdf_path, cache_dir=args.out, pages=pages)
    print(f"OK — {len(ocr.get('pages', []))} pages in {args.out / 'raw_ocr.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
