"""Persistence helpers — `rebuild_history` and `init_schema`."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from src.ui.persistence import init_schema, parse_thread_metadata, rebuild_history


def _step(role: str, content: str, ts: str) -> dict:
    step_type = "user_message" if role == "user" else "assistant_message"
    return {"type": step_type, "output": content, "createdAt": ts}


def test_rebuild_history_pairs_user_and_assistant():
    thread = {
        "steps": [
            _step("user", "what is photosynthesis?", "2026-05-18T10:00:00"),
            _step("assistant", "Plants use sunlight…", "2026-05-18T10:00:05"),
            _step("user", "quiz me", "2026-05-18T10:01:00"),
            _step("assistant", "Question 1: …", "2026-05-18T10:01:05"),
        ],
    }
    history = rebuild_history(thread, max_turns=5)
    assert history == [
        {"role": "user", "content": "what is photosynthesis?"},
        {"role": "assistant", "content": "Plants use sunlight…"},
        {"role": "user", "content": "quiz me"},
        {"role": "assistant", "content": "Question 1: …"},
    ]


def test_rebuild_history_sorts_by_created_at():
    # Out-of-order input — rebuild must sort chronologically.
    thread = {
        "steps": [
            _step("assistant", "second-a", "2026-05-18T10:01:05"),
            _step("user", "first-u", "2026-05-18T10:00:00"),
            _step("user", "second-u", "2026-05-18T10:01:00"),
            _step("assistant", "first-a", "2026-05-18T10:00:05"),
        ],
    }
    history = rebuild_history(thread, max_turns=5)
    assert [h["content"] for h in history] == [
        "first-u",
        "first-a",
        "second-u",
        "second-a",
    ]


def test_rebuild_history_caps_to_max_turns():
    steps = []
    for i in range(10):
        ts_u = f"2026-05-18T10:{i:02d}:00"
        ts_a = f"2026-05-18T10:{i:02d}:30"
        steps.append(_step("user", f"q{i}", ts_u))
        steps.append(_step("assistant", f"a{i}", ts_a))
    history = rebuild_history({"steps": steps}, max_turns=3)
    # 3 turns × 2 messages = 6 entries, taken from the tail.
    assert len(history) == 6
    assert history[0] == {"role": "user", "content": "q7"}
    assert history[-1] == {"role": "assistant", "content": "a9"}


def test_rebuild_history_ignores_non_message_steps():
    thread = {
        "steps": [
            _step("user", "hi", "2026-05-18T10:00:00"),
            {"type": "run", "output": "internal", "createdAt": "2026-05-18T10:00:01"},
            {"type": "llm", "output": "raw", "createdAt": "2026-05-18T10:00:02"},
            _step("assistant", "hey", "2026-05-18T10:00:03"),
        ],
    }
    history = rebuild_history(thread, max_turns=5)
    assert history == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hey"},
    ]


def test_rebuild_history_empty_thread():
    assert rebuild_history({"steps": []}, max_turns=5) == []
    assert rebuild_history({}, max_turns=5) == []


def test_parse_thread_metadata_handles_sqlite_json_string():
    # SQLite returns metadata as a JSON-encoded string (no native JSON
    # type); on_chat_resume must coerce to a dict.
    raw = '{"grade": 7, "subject": "math"}'
    assert parse_thread_metadata(raw) == {"grade": 7, "subject": "math"}


def test_parse_thread_metadata_handles_dict_pass_through():
    # PostgreSQL/JSONB returns a dict directly — no-op.
    raw = {"grade": 4, "subject": "arabic"}
    assert parse_thread_metadata(raw) is raw


def test_parse_thread_metadata_handles_none_and_garbage():
    assert parse_thread_metadata(None) == {}
    assert parse_thread_metadata("") == {}
    assert parse_thread_metadata("not-json") == {}
    # A JSON array is valid JSON but not a dict — should fall through.
    assert parse_thread_metadata("[1, 2, 3]") == {}


def test_init_schema_creates_all_tables(tmp_path: Path):
    db = tmp_path / "chats.db"
    init_schema(db)
    with sqlite3.connect(db) as conn:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {"users", "threads", "steps", "elements", "feedbacks"} <= names


def test_steps_table_covers_every_stepdict_field(tmp_path: Path):
    """Guard against silent column drift across Chainlit upgrades.

    SQLAlchemyDataLayer.create_step builds INSERT columns dynamically
    from whichever StepDict keys are non-None — so a missing column
    only surfaces at runtime as a `no such column` warning that the
    data layer swallows. This test pins the schema to StepDict's
    declared field set.
    """
    from chainlit.step import StepDict

    db = tmp_path / "chats.db"
    init_schema(db)
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute('PRAGMA table_info("steps")')}

    expected = set(StepDict.__annotations__.keys()) - {"feedback"}
    missing = expected - cols
    assert not missing, f"steps table is missing columns: {sorted(missing)}"


def test_init_schema_is_idempotent(tmp_path: Path):
    db = tmp_path / "chats.db"
    init_schema(db)
    # Insert a row, re-apply schema, confirm the row survives.
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO users (id, identifier, metadata) VALUES (?, ?, ?)",
            ("u1", "local", "{}"),
        )
        conn.commit()
    init_schema(db)
    with sqlite3.connect(db) as conn:
        rows = list(conn.execute("SELECT identifier FROM users"))
    assert rows == [("local",)]
