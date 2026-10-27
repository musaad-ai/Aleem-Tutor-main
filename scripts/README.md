# `scripts/` — developer helpers

Standalone CLIs that sit outside the importable `src/` tree. Run from
the repo root.

| Script             | What it does                                                                                                      |
| ------------------ | ----------------------------------------------------------------------------------------------------------------- |
| `smoke_run.py`     | Programmatic end-to-end run of the workflow-sandbox agent — no Chainlit, no UI. Prints the final answer, tool calls, verifier verdict, citation flags, and latency. |
| `ocr_probe.py`     | OCR-only probe over a PDF (no chunking, no Chroma writes). Cheap iteration tool for inspecting Mistral output.    |

## `smoke_run.py`

```bash
uv run python scripts/smoke_run.py "ما هي الدولة الأموية؟" \
    --grade 8 \
    --subject social_studies
```

Drives `run_agent(...)` against the configured backend. Useful for
verifying prompt changes, the verifier path, or the ceiling-refusal
shape without spinning up the UI. Set `llm.backend: fake` in
`config.yaml` for a zero-cost run (returns a canned answer with no
tool calls); set `llm.backend: openrouter` + `OPENROUTER_API_KEY` in
`.env` for a real tool-calling run.

- `--grade` choices: `4 | 7 | 8 | 10`.
- `--subject` defaults to `islamic_studies`; pass any of the five
  `SUBJECTS` values.

## `ocr_probe.py`

```bash
uv run python scripts/ocr_probe.py path/to/book.pdf \
    --pages 0-2 \
    --out data/processed/_probe/
```

Calls `src.ingest.ocr.ocr_book` directly and dumps `raw_ocr.json` (plus
per-page markdown via D15) so you can inspect what Mistral actually
returns before committing to a full ingest. Cost: ~$0.003 for 3 pages.

`--pages` accepts the same syntax as the main CLI: `0-2`, `0,1,2`, or
`0-2,5,7-8`.

## Not here

- The full ingest CLI — that's `python -m src.ingest`, owned by
  `src/ingest/__main__.py`.
- Chroma collection setup — `python -m src.retrieval.init_chroma`.
