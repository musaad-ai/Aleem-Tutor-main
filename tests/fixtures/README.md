# `tests/fixtures/` — real captured OCR output

Small, real Mistral OCR responses used by the ingest test suite so the
chunker is exercised against the actual schema, not a synthetic guess.

| File                  | What it is                                                                                                   |
| --------------------- | ------------------------------------------------------------------------------------------------------------ |
| `ocr_k05_p0-2.json`   | First 3 pages of the K05 Grade-5 sample. ~3.5 KB. Includes the cover + inner front matter, 3 annotated images. |

## How `ocr_k05_p0-2.json` was captured

```bash
uv run python scripts/ocr_probe.py \
    "Data/Books/f0e95389-GE-PE-K05-SM1-TFML_first20.pdf" \
    --pages 0-2 \
    --out data/processed/_probe/
cp data/processed/_probe/raw_ocr.json tests/fixtures/ocr_k05_p0-2.json
```

The probe burns ~$0.003 of Mistral OCR credit. The capture is committed
once and shared by every test — replay is free.

## When to regenerate

Only if Mistral changes the OCR response schema in a breaking way. The
chunker's contract is the response shape, not the specific text — a
schema-stable Mistral upgrade does not require re-capture.

Do **not** add the K05 PDF to git (`Data/` is gitignored). The fixture
is the only artifact that ships.
