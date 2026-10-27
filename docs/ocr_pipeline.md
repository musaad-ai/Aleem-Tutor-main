# OCR + Ingestion Pipeline

Turns a Saudi Ministry of Education textbook PDF into searchable, grade-scoped chunks in a vector database.

## Inputs
A single textbook PDF, plus its grade (4, 7, 8, or 10), subject, human-readable book name, and a stable `book_id`.

## Stages (in order)

1. **PDF intake** — the textbook PDF is read from disk and hashed with SHA-256; the hash is the cache key so re-running on the same file is free.
2. **Upload to Mistral Files API** — the PDF is uploaded with `purpose="ocr"`, returning a `file_id`, which is exchanged for a short-lived signed URL.
3. **OCR with Mistral OCR** — the signed URL is sent to `mistral-ocr-latest`, which returns per-page markdown for the entire book (logical RTL order for Arabic, headings preserved).
4. **Cache the raw OCR** — the full Mistral response is written atomically to `raw_ocr.json`, alongside per-page `.md` files and a combined `book.md` for human inspection.
5. **Page-level chunking** — each OCR'd page becomes one `Chunk`; blank pages are dropped, image placeholders are stripped, headers and footers stay inline for retrieval context.
6. **Metadata stamping** — every chunk is tagged with grade, subject, book, `book_id`, and 0-indexed page number; chunk IDs are deterministic (`{book_id}__p{N}`).
7. **Delete-then-add into Chroma** — any prior chunks for this `book_id` are deleted from the grade-specific Chroma collection (`grade_4`, `grade_7`, `grade_8`, `grade_10`) so stale pages can't linger.
8. **Embed with Jina-v4** — chunks are embedded in batches (default 16) by the `jinaai/jina-embeddings-v4` model and written into Chroma in the same call.
9. **Result** — an `IngestResult` summary is returned: book ID, pages OCR'd, chunks written, and the target Chroma collection.

## Outputs
- A populated grade-scoped Chroma collection ready for retrieval.
- On-disk cache: `upload.json`, `raw_ocr.json`, per-page markdown, full-book markdown.

## Key properties
- **Idempotent**: same PDF + same `book_id` → no API calls on re-run.
- **Grade-isolated**: each grade has its own Chroma collection; cross-grade leakage is impossible.
- **Resumable**: OCR cache survives Chroma write failures; one re-run recovers.
