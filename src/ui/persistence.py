"""Local chat-history persistence for the Aleem Chainlit UI.

Wraps Chainlit's `SQLAlchemyDataLayer` over a local SQLite file
(`./.aleem/chats.db`) and supplies two helpers that `app.py` uses:

- `make_data_layer()` — factory registered via `@cl.data_layer`. Ensures
  the SQLite file exists, applies the schema in `schema.sql` once, and
  returns a configured `SQLAlchemyDataLayer`.
- `rebuild_history(thread, max_turns)` — pure function that walks a
  resumed `ThreadDict` and rebuilds the `[{role, content}, ...]` list
  the agentic pipeline reads from `cl.user_session["history"]`.

The schema is applied synchronously at process start (`init_schema`),
not lazily, so the very first request can write without racing with
DDL. The file is gitignored under `.aleem/`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_SCHEMA_PATH = _HERE / "schema.sql"

DB_PATH: Path = _REPO_ROOT / ".aleem" / "chats.db"

# Roles persisted by Chainlit on cl.Message.send(): user-typed messages
# land as step.type == "user_message", assistant replies as
# "assistant_message". Other step types (run / tool / llm) are pipeline
# bookkeeping and must not enter the rewrite-node history window.
_USER_STEP_TYPE = "user_message"
_ASSISTANT_STEP_TYPE = "assistant_message"


def init_schema(db_path: Path = DB_PATH) -> None:
    """Apply schema.sql to the SQLite file. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
        conn.commit()


def make_data_layer() -> SQLAlchemyDataLayer:
    """Factory registered with `@cl.data_layer`.

    Called once by Chainlit on startup. We apply the schema here (rather
    than in a background task) so the very first user message can write
    without racing with DDL.
    """
    init_schema(DB_PATH)
    conninfo = f"sqlite+aiosqlite:///{DB_PATH}"
    return SQLAlchemyDataLayer(conninfo=conninfo)


def parse_thread_metadata(raw: Any) -> dict[str, Any]:
    """Coerce a thread's metadata blob to a dict.

    SQLite has no native JSON type, so the data layer round-trips
    metadata as a `json.dumps(...)` string. PostgreSQL/JSONB would
    return a dict directly; on SQLite we have to deserialize. Also
    handles None and malformed JSON without raising.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def rebuild_history(
    thread: dict[str, Any],
    max_turns: int,
) -> list[dict[str, str]]:
    """Rebuild `cl.user_session["history"]` from a resumed thread.

    Walks `thread["steps"]`, keeps only user/assistant messages, sorts by
    `createdAt` (Chainlit stores ISO-8601 strings — lexicographic sort
    is chronological), maps to `{"role": ..., "content": ...}`, and
    caps to `max_turns * 2` from the tail (each turn is user + assistant).
    """
    steps = thread.get("steps") or []

    pairs: list[tuple[str, dict[str, str]]] = []
    for step in steps:
        step_type = step.get("type")
        if step_type == _USER_STEP_TYPE:
            role = "user"
        elif step_type == _ASSISTANT_STEP_TYPE:
            role = "assistant"
        else:
            continue
        content = step.get("output") or ""
        created = step.get("createdAt") or ""
        pairs.append((created, {"role": role, "content": content}))

    pairs.sort(key=lambda p: p[0])
    history = [entry for _, entry in pairs]
    cap = max(0, max_turns) * 2
    if cap and len(history) > cap:
        history = history[-cap:]
    return history
