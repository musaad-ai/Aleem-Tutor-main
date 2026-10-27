"""Phase 1 of ingest: PDF -> Mistral OCR -> raw_ocr.json + markdown dumps.

Decisions implemented here:
- D3 content-hashed caching keyed on the PDF's SHA-256.
- D4 Files API upload + signed-URL flow (not base64 inline).
- D5 OCR call options, extended with optional `bbox_annotation_format`
  per the user-locked annotate_images flag.
- D12 tenacity wrapping that mirrors RESPONSE_WORKFLOW.md L20, plus the
  three hardening tweaks (atomic cache writes, 4xx denylist except 429,
  retry logging via `before_sleep_log`).
- D15 per-page + book.md dumps from the raw markdown.
- D17 INFO-level stage logging on `aleem.ingest.ocr`.

The single entry point is `ocr_book(...)`. Everything else is private.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mistralai.client import Mistral
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.config import settings

load_dotenv()

log = logging.getLogger("aleem.ingest.ocr")

_client: Mistral | None = None


def _get_client() -> Mistral:
    """Construct a Mistral client lazily so tests can import without a key (D4)."""
    global _client
    if _client is None:
        key = os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise RuntimeError(
                "MISTRAL_API_KEY not set. Add it to .env "
                "(template in .env.example). Get a key at "
                "https://console.mistral.ai/api-keys/"
            )
        _client = Mistral(api_key=key)
    return _client


def _pdf_sha256(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2))


def _is_persistent(exc: BaseException) -> bool:
    """A 4xx that won't self-heal — don't burn retries on it. 429 stays retryable."""
    status = getattr(exc, "status_code", None)
    if status is None:
        raw = getattr(exc, "raw_response", None)
        status = getattr(raw, "status_code", None)
    if status is None:
        return False
    return 400 <= status < 500 and status != 429


def _should_retry(exc: BaseException) -> bool:
    return not _is_persistent(exc)


_retry_upload = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=10),
    retry=retry_if_exception(_should_retry),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)

_retry_ocr = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=2, max=30),
    retry=retry_if_exception(_should_retry),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True,
)


# Mistral's bbox_annotation_format accepts only json_schema. This shape
# stays small on purpose — bigger schemas inflate every per-image call.
_IMAGE_ANNOTATION_SCHEMA: dict[str, Any] = {
    "name": "image_annotation",
    "schema_definition": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": (
                    "One-sentence description of what the image shows, "
                    "in the same language as the surrounding page."
                ),
            },
            "kind": {
                "type": "string",
                "description": (
                    "Image type — e.g. diagram, chart, table, photo, "
                    "illustration, formula, map."
                ),
            },
        },
        "required": ["description", "kind"],
        "additionalProperties": False,
    },
    "strict": True,
}


@_retry_upload
def _upload(client: Mistral, pdf_path: Path) -> str:
    size_mb = pdf_path.stat().st_size / 1e6
    log.info("uploading %s (%.1f MB)", pdf_path.name, size_mb)
    with pdf_path.open("rb") as f:
        resp = client.files.upload(
            file={"file_name": pdf_path.name, "content": f},
            purpose="ocr",
        )
    log.info("uploaded -> file_id=%s", resp.id)
    return resp.id


@_retry_upload
def _signed_url(client: Mistral, file_id: str) -> str:
    return client.files.get_signed_url(file_id=file_id).url


@_retry_ocr
def _call_ocr(
    client: Mistral,
    signed_url: str,
    *,
    pages: list[int] | None,
    annotate_images: bool,
) -> dict[str, Any]:
    log.info(
        "OCR start (model=mistral-ocr-latest, pages=%s, annotate_images=%s)",
        "all" if pages is None else pages,
        annotate_images,
    )
    kwargs: dict[str, Any] = {
        "model": "mistral-ocr-latest",
        "document": {"type": "document_url", "document_url": signed_url},
        "include_image_base64": False,
    }
    if pages is not None:
        kwargs["pages"] = pages
    if annotate_images:
        kwargs["bbox_annotation_format"] = {
            "type": "json_schema",
            "json_schema": _IMAGE_ANNOTATION_SCHEMA,
        }
    resp = client.ocr.process(**kwargs)
    return resp.model_dump(mode="json")


def _dump_markdown(ocr: dict[str, Any], cache_dir: Path) -> int:
    """D15: per-page raw markdown plus a concatenated book.md."""
    pages_dir = cache_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    for page in ocr.get("pages", []):
        index = int(page["index"])
        md = page.get("markdown") or ""
        _atomic_write_text(pages_dir / f"p{index:03d}.md", md)
        parts.append(f"<!-- page {index} -->\n{md}")
    _atomic_write_text(cache_dir / "book.md", "\n\n".join(parts) + "\n")
    return len(parts)


def ocr_book(
    pdf_path: str | Path,
    *,
    cache_dir: Path,
    pages: list[int] | None = None,
    annotate_images: bool | None = None,
) -> dict[str, Any]:
    """OCR a PDF via Mistral with content-hashed caching (D3-D5, D12, D15).

    Returns the OCR response as a plain dict (pydantic model_dump). Safe to
    call twice on the same `(pdf_path, cache_dir)` — the second call serves
    from cache if `pdf_sha256` matches, with no network round-trips.
    """
    pdf_path = Path(pdf_path)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if annotate_images is None:
        annotate_images = settings.ingestion.annotate_images

    upload_meta_path = cache_dir / "upload.json"
    raw_ocr_path = cache_dir / "raw_ocr.json"

    current_sha = _pdf_sha256(pdf_path)

    file_id: str | None = None
    if upload_meta_path.exists():
        meta = json.loads(upload_meta_path.read_text(encoding="utf-8"))
        if meta.get("pdf_sha256") == current_sha:
            file_id = meta["file_id"]
            log.info("upload cache hit (file_id=%s)", file_id)
        else:
            log.warning(
                "PDF sha256 mismatch (%s -> %s) — re-uploading + re-OCRing",
                str(meta.get("pdf_sha256", "?"))[:8],
                current_sha[:8],
            )
            if raw_ocr_path.exists():
                raw_ocr_path.unlink()

    client_needed = file_id is None or not raw_ocr_path.exists()
    client = _get_client() if client_needed else None

    if file_id is None:
        assert client is not None
        file_id = _upload(client, pdf_path)
        _atomic_write_json(
            upload_meta_path,
            {
                "file_id": file_id,
                "uploaded_at": datetime.now(UTC).isoformat(),
                "pdf_path": str(pdf_path),
                "pdf_sha256": current_sha,
                "pdf_size_bytes": pdf_path.stat().st_size,
            },
        )

    if raw_ocr_path.exists():
        log.info("OCR cache hit (raw_ocr.json, pdf_sha256=%s)", current_sha[:8])
        ocr = json.loads(raw_ocr_path.read_text(encoding="utf-8"))
    else:
        assert client is not None
        signed = _signed_url(client, file_id)
        ocr = _call_ocr(
            client,
            signed,
            pages=pages,
            annotate_images=annotate_images,
        )
        log.info("OCR done — %d pages", len(ocr.get("pages", [])))
        _atomic_write_json(raw_ocr_path, ocr)
        log.info("wrote raw_ocr.json (%.1f KB)", raw_ocr_path.stat().st_size / 1024)

    n_md = _dump_markdown(ocr, cache_dir)
    log.info("wrote %d page .md files + book.md", n_md)

    return ocr
