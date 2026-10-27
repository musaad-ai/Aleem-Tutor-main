# `src/ingest/` — PDF → OCR → chunk → embed → Chroma

Turns a Ministry-of-Education textbook PDF into per-grade Chroma chunks
the retrieve node can query. The design is locked in
[`OCR_implementation.md`](../../docs/OCR_implementation.md); this folder is
just the execution.

## Files

| File           | What it does                                                                                                                 |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `types.py`     | `Chunk` (id, text, metadata) and `IngestResult` dataclasses.                                                                 |
| `ocr.py`       | `ocr_book()` — Files-API upload, signed-URL, Mistral OCR with image annotation, SHA-256-keyed caching, atomic writes. (D3–D5, D12, D15) |
| `chunk.py`     | `chunks_from_ocr()` — page-based chunking, image annotations inlined as `[Image: …]`, blank-page skip. (D6–D9)               |
| `load.py`      | `load_chunks()` — delete-by-`book_id` then batched `collection.add()`. (D10, D11)                                            |
| `__init__.py`  | `ingest_book()` orchestrator with up-front validation. (D13)                                                                 |
| `__main__.py`  | argparse CLI — see "How to use" below.                                                                                       |

## How to use

### Ingest a whole book

```bash
uv run python -m src.ingest "Data/Books/Grade08/somebook.pdf" \
    --grade 8 \
    --subject math \
    --book "Math — Grade 8, Semester 1" \
    --book-id grade8_math_sem1
```

Logs each stage (`uploading → uploaded → OCR start → OCR done → chunking
→ embedding → loaded`) and prints the `IngestResult` JSON on stdout.
Safe to re-run — content-hashed cache (D3) means the second call serves
OCR from `raw_ocr.json` with no Mistral spend.

### Iterate cheaply during development

```bash
# OCR-only probe — no chunking, no Chroma writes, ~$0.003 per call
uv run python scripts/ocr_probe.py path/to/book.pdf --pages 0-2

# Full pipeline but only the first 11 pages (D16). Sibling cache dir.
uv run python -m src.ingest book.pdf --grade 8 --subject math \
    --book "..." --book-id grade8_math_sem1 \
    --pages 0-10
```

### Cache layout

```
data/processed/grade_{N}/{subject}/{book_id}/
├── upload.json     # {file_id, pdf_sha256, ...} — keyed cache (D3)
├── raw_ocr.json    # full Mistral OCR response (D3)
├── book.md         # concatenated raw markdown for grepping (D15)
└── pages/
    ├── p000.md     # per-page raw markdown — image placeholders intact
    ├── p001.md
    └── ...
```

The `.md` files are raw (pre-cleanup) on purpose — the cleaned text with
`[Image: …]` substitutions only lives inside Chroma chunks.

## Env vars

| Var               | Effect                                       | Default |
| ----------------- | -------------------------------------------- | ------- |
| `MISTRAL_API_KEY` | Required to run any OCR call. Lazy-checked.  | not set |

`OCR_implementation.md` D5 + D11 cover the config knobs in `config.yaml`
(`ingestion.embed_batch_size`, `ingestion.annotate_images`).

## Not here

- Embedding model itself — `src/retrieval/embeddings.py`.
- Chroma collection helpers — `src/retrieval/chroma_client.py`.
- The query path that consumes these chunks — `src/graph/tools.py` (the agent's `retrieve` tool).
