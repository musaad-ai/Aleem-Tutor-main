"""Phase 2 of ingest: parsed OCR -> list[Chunk].

Decisions implemented here:
- D6 page-based chunking (1 page = 1 chunk in v1).
- D7 markdown cleaning: image placeholders either stripped or — when
  bbox annotation is present — replaced inline with `[Image: <desc>]`
  so figures are retrievable. Blank pages produce no chunk.
- D8 metadata schema with empty defaults for chapter/lesson_title and
  `content_type="lesson_body"`. 0-indexed `page` matches the Mistral
  convention; UI renders page + 1.
- D9 deterministic IDs: `f"{book_id}__p{page.index}"`.

Pure function — no network, no Chroma, no env vars. Tests run against
`tests/fixtures/ocr_k05_p0-2.json` (captured from the validation probe).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from .types import Chunk

log = logging.getLogger("aleem.ingest.chunk")

_IMG_PLACEHOLDER = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)]*)\)")


def _annotation_text(annotation_json: str | None) -> str | None:
    """Pull the human description out of a bbox-annotation JSON string.

    Mistral fills `image_annotation` with the model_dump of the schema we
    sent in `bbox_annotation_format`. Our schema asks for `description`
    + `kind`; description is what we want for retrieval.
    """
    if not annotation_json:
        return None
    try:
        data = json.loads(annotation_json)
    except json.JSONDecodeError:
        return None
    desc = data.get("description") if isinstance(data, dict) else None
    return desc.strip() if isinstance(desc, str) and desc.strip() else None


def _clean_page_markdown(md: str, images: list[dict[str, Any]]) -> str:
    """D7: strip image placeholders; inline annotations when present.

    The placeholder src/alt typically equals the image's `id` (e.g.
    `![img-0.jpeg](img-0.jpeg)`), so we match on either.
    """
    by_id: dict[str, str] = {}
    for img in images:
        text = _annotation_text(img.get("image_annotation"))
        if text:
            by_id[img["id"]] = text

    def _replace(m: re.Match[str]) -> str:
        src = m.group("src").strip()
        alt = m.group("alt").strip()
        key = src or alt
        text = by_id.get(key) or by_id.get(alt) or by_id.get(src)
        return f"[Image: {text}]" if text else ""

    return _IMG_PLACEHOLDER.sub(_replace, md).strip()


def chunks_from_ocr(
    ocr: dict[str, Any],
    *,
    grade: int,
    subject: str,
    book: str,
    book_id: str,
) -> list[Chunk]:
    """Turn a Mistral OCRResponse dict into a list of Chunks ready for Chroma.

    One Chunk per non-blank page. Blank pages (covers, separators, empty
    ToC) are dropped — they have no semantic content and would only
    pollute retrieval.
    """
    chunks: list[Chunk] = []
    blanks = 0
    for page in ocr.get("pages", []):
        index = int(page["index"])
        raw_md = page.get("markdown") or ""
        cleaned = _clean_page_markdown(raw_md, page.get("images") or [])
        if not cleaned:
            blanks += 1
            continue

        metadata: dict[str, Any] = {
            "grade": grade,
            "subject": subject,
            "book": book,
            "book_id": book_id,
            "page": index,
            "chapter": "",
            "lesson_title": "",
            "content_type": "lesson_body",
        }
        chunks.append(
            Chunk(id=f"{book_id}__p{index}", text=cleaned, metadata=metadata)
        )

    log.info(
        "chunking — %d chunks (%d blank pages skipped)",
        len(chunks),
        blanks,
    )
    return chunks
