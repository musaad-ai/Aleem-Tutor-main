"""Tests for load_chunks with a fake Chroma collection."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.config import IngestionConfig
from src.ingest import load as load_module
from src.ingest.load import load_chunks
from src.ingest.types import Chunk


def _set_batch_size(monkeypatch, n: int) -> None:
    new_settings = replace(
        load_module.settings,
        ingestion=IngestionConfig(embed_batch_size=n, annotate_images=True),
    )
    monkeypatch.setattr(load_module, "settings", new_settings)


class FakeCollection:
    name = "grade_8"

    def __init__(self):
        self.added_batches: list[dict] = []
        self.deletes: list[dict | None] = []

    def delete(self, where=None):  # noqa: ANN001
        self.deletes.append(where)

    def add(self, *, ids, documents, metadatas):  # noqa: ANN001
        self.added_batches.append(
            {"ids": list(ids), "documents": list(documents), "metadatas": list(metadatas)}
        )


def _chunk(i: int, book_id: str = "bid") -> Chunk:
    return Chunk(
        id=f"{book_id}__p{i}",
        text=f"text {i}",
        metadata={"book_id": book_id, "page": i, "grade": 8},
    )


@pytest.fixture
def fake_collection(monkeypatch):
    col = FakeCollection()
    monkeypatch.setattr(load_module, "get_collection", lambda grade: col)
    return col


def test_delete_then_add_ordering(monkeypatch, fake_collection):
    _set_batch_size(monkeypatch, 16)
    chunks = [_chunk(i) for i in range(3)]
    n = load_chunks(chunks, grade=8)
    assert n == 3
    assert fake_collection.deletes == [{"book_id": "bid"}]
    assert len(fake_collection.added_batches) == 1
    assert fake_collection.added_batches[0]["ids"] == ["bid__p0", "bid__p1", "bid__p2"]


def test_batches_respect_embed_batch_size(monkeypatch, fake_collection):
    _set_batch_size(monkeypatch, 2)
    chunks = [_chunk(i) for i in range(5)]
    load_chunks(chunks, grade=8)
    # 5 chunks / batch 2 = 3 batches: [0,1], [2,3], [4]
    assert [len(b["ids"]) for b in fake_collection.added_batches] == [2, 2, 1]
    assert fake_collection.added_batches[0]["ids"] == ["bid__p0", "bid__p1"]
    assert fake_collection.added_batches[-1]["ids"] == ["bid__p4"]


def test_empty_chunks_skip_delete_and_add(monkeypatch, fake_collection):
    _set_batch_size(monkeypatch, 16)
    n = load_chunks([], grade=8)
    assert n == 0
    assert fake_collection.deletes == []
    assert fake_collection.added_batches == []
