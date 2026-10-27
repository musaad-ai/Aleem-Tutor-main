"""Tests for ocr_book caching + atomic writes — no network."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ingest import ocr as ocr_module
from src.ingest.ocr import (
    _atomic_write_text,
    _is_persistent,
    _pdf_sha256,
    ocr_book,
)

# -- atomic writes -------------------------------------------------------


def test_atomic_write_overwrites_via_rename(tmp_path):
    p = tmp_path / "x.json"
    _atomic_write_text(p, '{"v": 1}')
    _atomic_write_text(p, '{"v": 2}')
    assert json.loads(p.read_text()) == {"v": 2}
    # The .tmp scratch file should be gone after a clean rename.
    assert not (tmp_path / "x.json.tmp").exists()


def test_pdf_sha256_changes_with_content(tmp_path):
    a = tmp_path / "a.pdf"
    a.write_bytes(b"hello")
    b = tmp_path / "b.pdf"
    b.write_bytes(b"world")
    assert _pdf_sha256(a) != _pdf_sha256(b)
    # Same content -> same hash.
    c = tmp_path / "c.pdf"
    c.write_bytes(b"hello")
    assert _pdf_sha256(a) == _pdf_sha256(c)


# -- 4xx denylist --------------------------------------------------------


def test_4xx_is_persistent_except_429():
    for code in (400, 401, 403, 404, 422):
        assert _is_persistent(_err(code)) is True
    assert _is_persistent(_err(429)) is False
    assert _is_persistent(_err(500)) is False
    assert _is_persistent(_err(503)) is False
    # Network-ish: no status_code attribute -> retry.
    assert _is_persistent(RuntimeError("connection reset")) is False


def _err(status: int) -> Exception:
    e = Exception(f"status {status}")
    e.status_code = status  # type: ignore[attr-defined]
    return e


# -- cache logic --------------------------------------------------------


class FakeClient:
    def __init__(self, raw_ocr: dict | None = None):
        self.raw_ocr = raw_ocr or {"pages": [], "model": "x", "usage_info": {}}
        self.upload_calls = 0
        self.ocr_calls = 0
        self.files = SimpleNamespace(
            upload=self._upload,
            get_signed_url=self._get_signed_url,
        )
        self.ocr = SimpleNamespace(process=self._process)

    def _upload(self, *, file, purpose):  # noqa: ANN001
        self.upload_calls += 1
        return SimpleNamespace(id="file-abc")

    def _get_signed_url(self, *, file_id, **_):  # noqa: ANN001
        return SimpleNamespace(url=f"https://signed/{file_id}")

    def _process(self, **kwargs):  # noqa: ANN001
        self.ocr_calls += 1
        # Mimic Pydantic model_dump
        return SimpleNamespace(model_dump=lambda **_kw: dict(self.raw_ocr))


@pytest.fixture
def fake_pdf(tmp_path) -> Path:
    p = tmp_path / "book.pdf"
    p.write_bytes(b"PDF-fake-content-v1")
    return p


@pytest.fixture
def patch_client(monkeypatch):
    fake = FakeClient(raw_ocr={
        "pages": [{"index": 0, "markdown": "hello", "images": []}],
        "model": "mistral-ocr-latest",
        "usage_info": {"pages_processed": 1},
    })
    monkeypatch.setattr(ocr_module, "_get_client", lambda: fake)
    # Reset module-level cached client so other tests don't interfere.
    monkeypatch.setattr(ocr_module, "_client", None)
    return fake


def test_first_run_uploads_and_calls_ocr(fake_pdf, tmp_path, patch_client):
    ocr = ocr_book(fake_pdf, cache_dir=tmp_path / "cache", annotate_images=False)
    assert patch_client.upload_calls == 1
    assert patch_client.ocr_calls == 1
    assert len(ocr["pages"]) == 1
    # Sidecar files exist.
    assert (tmp_path / "cache" / "upload.json").exists()
    assert (tmp_path / "cache" / "raw_ocr.json").exists()
    assert (tmp_path / "cache" / "pages" / "p000.md").exists()


def test_second_run_hits_cache_no_network(fake_pdf, tmp_path, patch_client):
    cache = tmp_path / "cache"
    ocr_book(fake_pdf, cache_dir=cache, annotate_images=False)
    pre_upload = patch_client.upload_calls
    pre_ocr = patch_client.ocr_calls
    ocr_book(fake_pdf, cache_dir=cache, annotate_images=False)
    assert patch_client.upload_calls == pre_upload
    assert patch_client.ocr_calls == pre_ocr


def test_pdf_sha_mismatch_invalidates_cache(fake_pdf, tmp_path, patch_client):
    cache = tmp_path / "cache"
    ocr_book(fake_pdf, cache_dir=cache, annotate_images=False)
    # Replace PDF content -> hash changes -> re-upload + re-OCR.
    fake_pdf.write_bytes(b"PDF-fake-content-v2-different")
    ocr_book(fake_pdf, cache_dir=cache, annotate_images=False)
    assert patch_client.upload_calls == 2
    assert patch_client.ocr_calls == 2


def test_annotate_images_passed_through_to_ocr_call(fake_pdf, tmp_path, monkeypatch):
    captured: dict = {}

    class Capturing(FakeClient):
        def _process(self, **kwargs):
            captured.update(kwargs)
            return super()._process(**kwargs)

    cap = Capturing()
    monkeypatch.setattr(ocr_module, "_get_client", lambda: cap)
    monkeypatch.setattr(ocr_module, "_client", None)

    ocr_book(fake_pdf, cache_dir=tmp_path / "c1", annotate_images=True)
    assert "bbox_annotation_format" in captured
    captured.clear()

    ocr_book(fake_pdf, cache_dir=tmp_path / "c2", annotate_images=False)
    assert "bbox_annotation_format" not in captured
