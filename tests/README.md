# `tests/` — pytest suite

Critical-path coverage for the tool-calling agent and the ingest pipeline.
**All tests run against `backend=fake` and a mocked Mistral client** — no
network, no API key, no token spend. The `_force_fake_backend` autouse
fixture in `conftest.py` rewrites `src.config.settings` for every test,
so a developer with a live `backend: openrouter` in `config.yaml` still
gets fully deterministic runs.

The agent test (`test_agent.py`) defines a tiny `FakeToolCallingModel`
(FakeMessagesListChatModel + passthrough `bind_tools`) and queues
hand-crafted `AIMessage`s so the `create_react_agent` loop is actually
exercised — tool call → tool execution → final answer.

## Files

| File                       | Covers                                                                                                                            |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `conftest.py`              | `_force_fake_backend` autouse fixture; `make_agent_state` factory; `stub_chunks` re-export from `src/graph/tools._STUB_CHUNKS`.    |
| `test_state.py`            | `Chunk` round-trip + `initial_agent_state()` defaults & history field.                                                            |
| `test_tools.py`            | `@tool retrieve()` — empty-Chroma stub fallback, ToolMessage formatting with rerank scores, global numbering across calls, context-var guard. |
| `test_parse.py`            | `parse_citations()` — in-range, dedup, out-of-range flag, `no_citations` flag, non-numeric bracket ignored.                       |
| `test_verifier.py`         | `VerifierDecision` schema + fake-backend canned on_topic verdict + empty-answer off_topic shortcut.                               |
| `test_agent.py`            | End-to-end agent loop with `FakeToolCallingModel` — tool call → cited answer, history injection, latency_ms recorded.             |
| `test_ceiling_refusal.py`  | §3 — agent hits `max_tool_calls` ceiling → refusal with topic suggestions; generic-fallback when no chunks were retrieved.        |
| `test_logging.py`          | `@timed` decorator unit — merges `latency_ms`, preserves sibling debug fields.                                                    |
| `test_logging_agent.py`    | New JSONL schema written by `run_agent` — required fields present, old-schema fields absent, tool-call detail captured.           |
| `test_ui_persistence.py`   | Chainlit history rebuild from persisted thread + thread-metadata parsing (grade / subject restore).                               |
| `test_ingest_types.py`     | `Chunk` / `IngestResult` frozen invariants (ingest is collaborator-owned, unchanged on this branch).                              |
| `test_ingest_chunk.py`     | D6–D9 — page-based chunks, blank-page skip, image-annotation inline, deterministic IDs. Runs against the captured K05 fixture.    |
| `test_ingest_load.py`      | D10–D11 — delete-by-`book_id`, batched `add`, empty-chunks guard. Fake Chroma collection.                                         |
| `test_ingest_ocr.py`       | D3 / D12 — atomic write, SHA-256 hash, 4xx denylist, upload cache, OCR cache, hash-mismatch invalidation, `annotate_images`.      |
| `test_ingest_cli.py`       | D13 — `--grade` choices restricted to `(4, 7, 8, 10)`, `--subject` validated, `--pages` parser.                                   |
| `fixtures/`                | Real Mistral OCR output captured from the validation probe — see `fixtures/README.md`.                                            |

## Run

```bash
# Full suite (≈4s, no network)
uv run pytest

# A single test file
uv run pytest tests/test_agent.py

# A single test
uv run pytest tests/test_parse.py::test_out_of_range_markers_are_flagged

# Verbose with print output
uv run pytest -vv -s
```

## Coverage philosophy

One test (or short cluster) per spec section. docs/WORKFLOW_SANDBOX.md sections
§3 (ceiling refusal), §4 (verifier), §3 layer 2 (parse) each have a file.
Old per-L (L1–L22) tests are gone — they targeted nodes that no longer
exist. If §13's "merge to main" criteria ever ship with an eval harness,
that lives in `eval/`, not here.

## Not here

- Live OpenRouter / Ollama tests — would require an API key. Verify
  manually via Chainlit or `scripts/smoke_run.py` (with `backend:
  openrouter`).
- UI / Chainlit tests — `cl.Message` mocking isn't worth the brittleness.
  The handler is exercised manually during demo runs.
- Coverage reports — add when CI lands, not before.
