"""L18 query logging — JSONL file + one-line stdout summary.

Two pieces:

- `@timed("phase_name")` — wraps an async coroutine and records its
  elapsed ms into `state.debug.latency_ms`. The workflow-sandbox path
  uses two phases: `agent_loop` (tool calls + generation) and `verifier`
  (post-hoc topical check). `total` is added by `run_agent()` outside
  the wrapped phases.

- `log_query(agent_state)` — called from `run_agent()` so every turn
  produces exactly one JSONL line and one short stdout summary,
  regardless of caller (Chainlit, smoke script, future API).

JSONL schema (workflow-sandbox shape — supersedes the old intent /
self_check / num_tasks fields):

    ts, session_id, query, grade, subject,
    tool_calls: [{query, num_chunks, rerank_scores}],
    answer_length, citations_count, citation_flags,
    verifier_verdict, verifier_reason,
    refused, refusal_reason,
    latency_ms: {agent_loop, verifier, total}

Stdout summary format:
    [18:31:02] grade=7 tools=2 verifier=on_topic 2160ms

`logs/` is gitignored; daily file rotation keeps the demo-day file easy
to identify.
"""

from __future__ import annotations

import functools
import json
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from src.config import REPO_ROOT

LOGS_DIR = REPO_ROOT / "logs"

NodeFn = Callable[[Any], Awaitable[dict | None]]


def timed(phase_name: str) -> Callable[[NodeFn], NodeFn]:
    """Wrap an async function so its latency lands in
    `state.debug.latency_ms[phase_name]`.

    Merges the wrapped function's `debug` update with the upstream debug
    dict so sibling fields (e.g. `tool_calls`) survive.
    """

    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapped(state, *args, **kwargs):
            t0 = time.perf_counter()
            update = await fn(state, *args, **kwargs) or {}
            latency = int((time.perf_counter() - t0) * 1000)

            if not isinstance(update, dict):
                return update

            existing = dict(state.get("debug") or {}) if hasattr(state, "get") else {}
            from_update = dict(update.get("debug") or {})
            merged: dict[str, Any] = {**existing, **from_update}

            latency_ms = dict(merged.get("latency_ms") or {})
            latency_ms[phase_name] = latency
            merged["latency_ms"] = latency_ms

            new_update = dict(update)
            new_update["debug"] = merged
            return new_update

        return wrapped

    return decorator


def _tool_call_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tc in state.get("tool_calls") or []:
        chunks = getattr(tc, "chunks", []) or []
        out.append(
            {
                "query": getattr(tc, "query", ""),
                "num_chunks": len(chunks),
                "rerank_scores": [
                    round(float(c.rerank_score), 4) for c in chunks
                ],
            }
        )
    return out


def _record(state: dict[str, Any]) -> dict[str, Any]:
    debug = state.get("debug") or {}
    return {
        "ts": datetime.now(tz=UTC).isoformat(),
        "session_id": uuid.uuid4().hex[:12],
        "query": state.get("user_query"),
        "grade": state.get("grade"),
        "subject": state.get("subject"),
        "tool_calls": _tool_call_records(state),
        "answer_length": len(state.get("final_answer") or ""),
        "citations_count": len(state.get("citations") or []),
        "citation_flags": list(state.get("citation_flags") or []),
        "verifier_verdict": state.get("verifier_verdict"),
        "verifier_reason": state.get("verifier_reason"),
        "refused": bool(state.get("refused")),
        "refusal_reason": state.get("refusal_reason") or None,
        "latency_ms": dict(debug.get("latency_ms") or {}),
    }


def _stdout_summary(rec: dict[str, Any]) -> str:
    ts = datetime.now().strftime("%H:%M:%S")
    grade = rec.get("grade") if rec.get("grade") is not None else "?"
    tools = len(rec.get("tool_calls") or [])
    verdict = rec.get("verifier_verdict") or "?"
    refused = " refused" if rec.get("refused") else ""
    total_ms = (rec.get("latency_ms") or {}).get("total", 0)
    return (
        f"[{ts}] grade={grade} tools={tools} verifier={verdict}"
        f"{refused} {total_ms}ms"
    )


def log_query(state: dict[str, Any]) -> None:
    """Write one JSONL line + print a one-line stdout summary."""
    rec = _record(state)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"queries-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(_stdout_summary(rec), file=sys.stderr)
