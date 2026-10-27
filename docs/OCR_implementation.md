# OCR + Ingestion — Implementation Plan

Replaces `BUILD_SPEC.md §4.1` (Qari → Mistral OCR). Working notes for the
ingestion half of Aleem: PDF → OCR → chunk → embed → Chroma.

Owner: Turki (taken over from collaborator).

Status legend: ✅ locked · 🟡 open · ⬜ deferred to v2.

---

## References

- **`docs/openapi.yaml`** — authoritative Mistral API spec, vendored
  in-repo. Everything we need is in there:
  - `POST /v1/ocr` endpoint definition at **line 2035**.
  - OCR-related schemas (`OCRRequest`, `OCRResponse`, `OCRPageObject`,
    `OCRImageObject`, `OCRTableObject`, `OCRPageDimensions`,
    `OCRPageConfidenceScores`, `OCRUsageInfo`) at **lines 11754–12110**.
  - Files API upload endpoint (`POST /v1/files`, `purpose: ocr`) — see
    grep `purpose: \[ocr\]` for the surrounding context.
  - Input document union (`FileChunk`, `DocumentURLChunk`,
    `ImageURLChunk`) at lines 11677, 7578, 7786.

  When a field's behavior is ambiguous, this file is the source of truth
  — cite line numbers in code comments or commit messages so future
  readers can verify.
- **Mistral hosted docs (companion to the spec):**
  https://docs.mistral.ai/studio-api/document-processing/basic_ocr
- **Python SDK:** `mistralai` (`uv add mistralai`).

---

## The goal

A single function the user calls per book:

```python
ingest_book(
    pdf_path="Data/Books/grade4_science_sem1.pdf",
    grade=4,
    subject="science",
    book="Science — Semester 1",
    book_id="grade4_science_sem1",
) -> IngestResult
```

After this returns successfully, the book's pages are sitting in the
`grade_4` Chroma collection, embedded with Jina-v4, ready for the
already-built query path (`src/graph/nodes/retrieve.py`).

---

## D1 — Function signature ✅

```python
def ingest_book(
    pdf_path: str | Path,
    *,
    grade: int,
    subject: str,    # one of SUBJECTS in src/retrieval/chroma_client.py
    book: str,       # human-readable, e.g. "Science — Semester 1"
    book_id: str,    # short stable id, e.g. "grade4_science_sem1"
    pages: list[int] | None = None,   # D16 — partial-book runs for cheap iteration
) -> IngestResult:
    ...
```

`pages` defaults to `None` (full book). When set, the orchestrator builds
a sibling cache dir (`{book_id}+pages{lo}-{hi}`) — see D16. Production
calls always leave `pages=None`; the flag exists for debugging.

`IngestResult` is a small dataclass:
```python
@dataclass
class IngestResult:
    book_id: str
    pages_ocred: int
    chunks_written: int
    chroma_collection: str   # e.g. "grade_4"
```

**Why explicit kwargs (not filename parsing or a manifest):**
- IEN filenames are opaque hashes (`f0e95389-GE-PE-K05-SM1-TFML.pdf`) — a
  parser would be fragile.
- Each grade has its own Chroma collection — wrong-grade ingestion is a
  silent correctness bug that must be visible at the call site.
- A manifest file (`books.yaml`) can be added later as a thin loop on top
  of this function.

---

## D2 — Pipeline of independently-callable phases ✅

`ingest_book` is a thin orchestrator over three pure functions, each in
its own file:

```
src/ingest/
├── __init__.py     # exports ingest_book() — orchestrator
├── __main__.py     # argparse CLI wrapper (D13)
├── types.py        # Chunk + IngestResult dataclasses
├── ocr.py          # ocr_book()       — Mistral OCR + caching
├── chunk.py        # chunks_from_ocr()— turn OCR response into Chunks
└── load.py         # load_chunks()    — embed with Jina-v4 + write to Chroma
```

```python
def ingest_book(pdf_path, *, grade, subject, book, book_id) -> IngestResult:
    cache_dir = Path(f"data/processed/grade_{grade}/{subject}/{book_id}")
    ocr      = ocr_book(pdf_path, cache_dir=cache_dir)
    chunks   = chunks_from_ocr(ocr, grade=grade, subject=subject,
                               book=book, book_id=book_id)
    written  = load_chunks(chunks, grade=grade)
    return IngestResult(book_id, len(ocr.pages), written, f"grade_{grade}")
```

**Why three phases, not monolithic:**
- OCR is the expensive step (paid API). Phase boundary = on-disk cache =
  chunker changes don't re-pay for OCR.
- v2 (lesson-based chunking) is a one-file swap of `chunk.py`.
- Each phase is a pure function → tests can run on fixtures, no network,
  no API key, no Chroma writes. Matches the existing 1.5s test suite.

### Types shared across phases

```python
# src/ingest/types.py
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class Chunk:
    """One unit of indexed content. v1: one Chunk per OCR'd page."""
    id: str                   # deterministic, per D9 → f"{book_id}__p{N}"
    text: str                 # cleaned page markdown, per D7
    metadata: dict[str, Any]  # schema matches src/retrieval/chroma_client.py:8-18

@dataclass(frozen=True)
class IngestResult:
    """Return value of ingest_book() — what the CLI prints."""
    book_id: str
    pages_ocred: int
    chunks_written: int
    chroma_collection: str    # e.g. "grade_4"
```

`Chunk.metadata` is a plain dict (not a typed schema) because that's
exactly what Chroma's `collection.add(metadatas=[...])` expects. The
field set is enforced by code review against `chroma_client.py:8-18`,
not by a runtime check.

---

## D3 — Caching: idempotent re-runs, content-hashed ✅

`ingest_book()` is safe to call twice on the same book. Two cache files
per book, both under `data/processed/grade_{N}/{subject}/{book_id}/`:

| File | Contents | Skips |
|---|---|---|
| `upload.json` | `{file_id, uploaded_at, pdf_path, pdf_sha256, pdf_size_bytes}` | re-uploading the PDF |
| `raw_ocr.json` | the full Mistral OCR response | re-calling the OCR API |

The signed URL used to call OCR is **not** cached — it's fetched fresh
each time `raw_ocr.json` misses, via
`client.files.get_signed_url(file_id=...)`. The call is cheap and signed
URLs expire after 24h by default, so caching them would buy nothing.

**Cache key is the PDF's SHA-256, not just the directory path.**
On every read, the current PDF's hash is computed and compared to
`upload.json:pdf_sha256`. Mismatch → log a warning and re-process.
This prevents the silent-stale-cache bug: swap `book.pdf` for an
updated version with the same `book_id` and the function correctly
re-OCRs instead of reading old content.

```python
def _pdf_sha256(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):   # 1 MB chunks
            h.update(chunk)
    return h.hexdigest()
```

**Cost:** SHA-256 over a 50 MB PDF takes ~200 ms. Negligible vs. OCR.

**Re-run behavior:**
- Same PDF, same `book_id` → both caches hit, no API calls.
- Different PDF, same `book_id` → hash mismatch detected, both upload
  and OCR re-run, caches overwritten atomically.
- Same PDF, different `book_id` → still re-uploads / re-OCRs (different
  cache dir). Acceptable: cross-`book_id` dedup is rare in practice and
  adding a global blob store is over-engineering for v1.

Force a fresh run any time by deleting the cache directory.

---

## D4 — Files API for upload, not base64 ✅

The PDF is uploaded to Mistral's Files API with `purpose="ocr"`, then a
signed URL is fetched for the returned `file_id` and passed to the OCR
call's `document` field. Two SDK calls in sequence:

```python
uploaded = client.files.upload(
    file={"file_name": pdf_path.name, "content": pdf_path.open("rb")},
    purpose="ocr",
)
signed = client.files.get_signed_url(file_id=uploaded.id)
document = {"type": "document_url", "document_url": signed.url}
```

**Why signed URL, not `{"type": "file", "file_id": ...}`:** the OpenAPI
spec defines both `FileChunk` (line 11677) and `DocumentURLChunk` (line
7578), so either *should* work on the wire. But every Mistral docs page
and cookbook uses the upload-then-signed-URL flow — sticking to it means
Mistral example code and support apply if anything breaks. Signed URLs
default to a 24-hour expiry; we consume them immediately, so expiry
isn't a factor.

**Why not base64 inline:**
- Textbook PDFs are 30–80 MB. Base64 encoding pushes JSON bodies past
  100 MB. Slow and risks request-size limits.
- Files API is what Mistral calls "cleanest for batch ingestion" — we're
  ingesting 15 books, that's batch.

**Cost:** uploading is free. OCR itself is paid — ~$1 per 1,000 pages
(~$0.25 per textbook, ~$3.75 for the full pilot corpus). Approach
A vs B doesn't change the OCR cost; it only changes how the bytes
arrive.

### Client initialization

One `Mistral` client instance per process, lazily constructed, reused by
both the upload and OCR calls. Lives at module scope in `src/ingest/ocr.py`.

```python
# src/ingest/ocr.py
import os
from mistralai import Mistral
from dotenv import load_dotenv

load_dotenv()  # python-dotenv is already a project dep

_client: Mistral | None = None

def _get_client() -> Mistral:
    global _client
    if _client is None:
        key = os.environ.get("MISTRAL_API_KEY")
        if not key:
            raise RuntimeError(
                "MISTRAL_API_KEY not set. Add it to .env "
                "(template in .env.example). Get a key at "
                "https://console.mistral.ai/api-keys/"
            )
        _client = Mistral(api_key=key)
    return _client
```

Lazy construction means tests (which never have an API key) can import
`src.ingest.ocr` without crashing — the error only fires when actual
Mistral work is attempted.

---

## D5 — OCR call options (locked for v1) ✅

```python
client.ocr.process(
    model="mistral-ocr-latest",
    document={"type": "document_url", "document_url": signed_url},
    include_image_base64=False,    # diagrams out of scope (BUILD_SPEC §4.1 / §5.3)
    # extract_header / extract_footer: omitted → headers/footers stay inline in
    #   markdown. Decision: keep them as context (chapter title, book name) for
    #   retrieval quality. See D7.
    # table_format: omitted → tables stay inline in markdown (no special path)
    # confidence_scores_granularity: omitted → keeps response payload small
    # pages: omitted by default → OCR the whole PDF
)
```

**What the OpenAPI spec confirms:** `page.index` starts from 0
(line 11892 of `docs/openapi.yaml`). `pages` parameter is also 0-indexed.

**Validation knob:** `pages=[0, 1, 2]` for cheap first-3-pages probes
during development. Wired through `ocr_book(..., pages=None)`.

---

## D6 — Page-based chunking for v1 (1 page = 1 chunk) ✅

For v1: one chunk per OCR'd page. Chunk metadata schema matches the
contract already locked in `src/retrieval/chroma_client.py:8-18`
(`page: int` works — no schema change).

**v2 (deferred ⬜):** lesson-based chunking, where a chunk = a whole
lesson even if it spans multiple pages. Requires a metadata schema
change (`page` → `page_start` + `page_end`). Single-file swap of
`chunk.py` when we get there.

**What we trade in v1:**
- Citations show page numbers, not lesson titles.
- The refusal path (L1) shows "page 7 of chapter X" instead of lesson
  titles, unless we get chapter/lesson metadata from D7 below.
- Long lessons split into multiple chunks (acceptable — they embed
  similarly and usually retrieve together).

---

## D7 — Chunk text cleaning ✅

The chunk's text is the page's markdown with **only image placeholders
stripped**. Headers and footers stay inline.

**Why keep header/footer inline:** they carry structural context
(chapter title, book name) that helps retrieval understand "what is
this page about?" Modern RAG research generally favors preserving
structural context in chunks. The trade-off — slight embedding
pollution from repeated strings within a single book — is acceptable at
the pilot's scale (4–5 books per grade collection).

**What we strip:** image placeholders like `![img-0.jpeg](img-0.jpeg)`.
These are dangling references with no semantic content; `img-0.jpeg`
appears identically across many chunks in many books → genuine noise.

**Blank pages:** if the cleaned markdown is empty/whitespace,
**no chunk is produced** for that page (covers, separators, empty
ToC pages).

```python
import re
_IMG = re.compile(r"!\[.*?\]\(.*?\)")

def _clean(md: str) -> str:
    return _IMG.sub("", md).strip()
```

---

## D8 — Chunk metadata fill-in ✅

For v1, `chapter`, `lesson_title`, and `content_type` are filled with
**default values** rather than extracted from the OCR output:

```python
metadata = {
    "grade": grade,
    "subject": subject,
    "book": book,           # human-readable, used in citations/refusals
    "book_id": book_id,     # stable handle, used for D10 delete + filtering
    "page": page.index,     # 0-indexed (Mistral spec); UI renders page + 1
    "chapter": "",
    "lesson_title": "",
    "content_type": "lesson_body",
}
```

**Page indexing convention:** `page` is stored as the raw 0-indexed
`page.index` from the Mistral response — no translation at write time.
Citation / log rendering adds 1 so users see human-friendly "page 1, 2,
3…" while metadata stays aligned with the API. This is the only place
the convention is pinned; downstream renderers (`generate.py`, refusal
bullets, ingestion logs) all derive from it.

**Why both `book` and `book_id`:** `book` is the display name shown in
citations ("Science — Semester 1, page 23"); `book_id` is the stable
short handle (`grade4_science_sem1`) used by D10 to delete a book's
prior chunks before re-ingestion. Without `book_id` in metadata, D10
would have to delete by display name — which isn't guaranteed unique
across re-editions / typos / collaborator copies. Update
`src/retrieval/chroma_client.py:8-18` docstring **and**
`src/graph/state.py::Chunk` (add `book_id: str = ""` + include it in
`as_metadata()`) in the same commit so the contract claim at
`state.py:6-8` ("Keep these in sync") stays honest. Default the new
field to empty string so the existing stub chunks in
`src/graph/nodes/retrieve.py` keep constructing without modification.

**Why empty:** reliable extraction of chapter/lesson titles from OCR
output requires either (i) consistent heading hierarchy from Mistral
(unverified — answered by the validation pass), or (ii) an LLM
post-pass per the original BUILD_SPEC §4.2 plan. Both are non-trivial.
Shipping v1 with empty fields is honest and avoids encoding wrong
metadata into Chroma.

**Cost in v1:**
- Refusal path (L1) cannot surface lesson titles. `src/graph/nodes/refuse.py:35-38`
  filters out chunks with empty `lesson_title`, so with all titles empty
  the refusal degrades to **just the canonical phrase** (no "Related
  topics in your textbook:" bullet list). Cleaner than fabricating a
  fake "page 23" bullet, but worth noting it's a real UX downgrade
  until D8 v2 lands.
- Citations are page-level only — *"Science Grade 4, page 23"* instead
  of *"Lesson 3: Photosynthesis, page 23"*.

**v2 plan ⬜:** add a metadata extraction step after OCR. Two options
to consider when we get there:
- Best-effort `#` / `##` heading scan with running state.
- LLM post-pass (one cheap call per page asking "what chapter / lesson
  is this?"). Better quality, paid.

The choice depends on what the validation pass reveals about Mistral's
heading consistency. Document the finding in this file when v1 lands.

---

## D9 — Chunk ID strategy: deterministic ✅

```python
chunk_id = f"{book_id}__p{page.index}"
```

Same page → same ID forever. Lets us inspect a specific chunk in Chroma
by ID later, and pairs with D10 for clean re-ingestion.

---

## D10 — Chroma write strategy: delete-then-add ✅

Before writing chunks for a book, delete any pre-existing chunks for
that book, then `collection.add(...)` the fresh set.

```python
def load_chunks(chunks: list[Chunk], *, grade: int) -> int:
    collection = get_collection(grade)

    book_id = chunks[0].metadata["book_id"] if chunks else None
    if book_id is not None:
        collection.delete(where={"book_id": book_id})

    collection.add(
        ids=[c.id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[c.metadata for c in chunks],
    )
    return len(chunks)
```

**Why delete by `book_id`, not `book`:** `book` is the human-readable
display name — not guaranteed unique across re-editions or
typos. Deleting by `book_id` (the stable short handle that D8 now
stores in metadata) means a re-ingest only ever nukes the chunks that
belong to that exact book.

**Why delete-then-add over `upsert`:** upsert silently leaves stale
chunks if a re-run produces fewer chunks than the previous run
(e.g. better blank-page detection drops 3 cover pages). Delete-then-add
removes that whole class of bug. Cost: one Chroma `delete` per book.

**Why `is not None` rather than `if book_id`:** an empty-string
`book_id` would be falsy and silently skip the delete, re-introducing
the stale-chunk bug this section exists to prevent. The up-front
validation in D13 (`if not book_id: raise ValueError`) makes the empty
case unreachable for real ingest paths; the `is not None` guard here
distinguishes "no chunks at all" from "we have chunks, run the delete"
without relying on truthiness.

**Safety:** if Chroma's `add` fails after the delete, the book's
chunks are temporarily gone — but the OCR result is still cached in
`raw_ocr.json`, so re-running `ingest_book()` recovers in one call.
No real data loss.

---

## D11 — Embedding batch size: default 16, configurable via config.yaml ✅

`load_chunks()` slices the chunks into batches of `settings.ingestion.embed_batch_size`
(default 16) before calling `collection.add()`.

```python
from src.config import settings

batch = settings.ingestion.embed_batch_size
for i in range(0, len(chunks), batch):
    slab = chunks[i : i + batch]
    collection.add(
        ids=[c.id for c in slab],
        documents=[c.text for c in slab],
        metadatas=[c.metadata for c in slab],
    )
```

**Why 16:** safe on M1/M2 Macs (MPS) and most consumer GPUs. Bigger
batches risk OOM on small GPUs; smaller batches are slower but
ingestion is a one-time, slow job — speed barely matters here.

**Why config.yaml, not env-var:** project convention per CLAUDE.md —
*"Config in `config.yaml`, secrets in `.env`."* Batch size isn't a
secret, so it belongs alongside the other runtime knobs
(`retrieval.top_k_retrieve`, `memory.max_turns`). Wiring is one new
section in `config.yaml`:

```yaml
ingestion:
  embed_batch_size: 16    # lower to 8 on small GPUs
```

…plus a new `IngestionConfig` dataclass in `src/config.py` mirroring
the shape of `MemoryConfig` (one int field). First person to hit OOM
edits one YAML line, same as every other tunable in the project.

This is independent from chunking (D6). Chunking decides *what* lands
in Chroma (1 chunk per page); batch size decides *how* the embedder
feeds chunks to the GPU. Same 200 chunks land in Chroma either way.

---

## D12 — Error handling: tenacity, matched to L20, with three hardening tweaks ✅

The Mistral file upload and OCR calls are wrapped with `tenacity`,
following the same pattern as `LLMClient` (RESPONSE_WORKFLOW.md L20):

- `stop_after_attempt=3`
- `wait=wait_exponential_jitter(initial=1, max=10)` for upload,
  `initial=2, max=30` for OCR (genuinely longer-running)
- Retry on transient errors (network, 5xx, timeouts, 429 rate limit)
- `reraise=True` — surface the real error after exhaustion

**Three hardening tweaks on top:**

1. **Atomic cache writes.** Write to `raw_ocr.json.tmp`, then `rename`.
   Prevents corrupt cache files if the process is killed mid-write.

   ```python
   def _atomic_write_text(path: Path, content: str) -> None:
       tmp = path.with_suffix(path.suffix + ".tmp")
       tmp.write_text(content)
       tmp.replace(path)
   ```

2. **Explicit 4xx denylist.** Don't retry auth errors, "file too large",
   or other client errors — they won't self-heal. **429 (rate limit) is
   the exception — that IS transient, so it stays retryable.**

   ```python
   def _is_persistent(exc) -> bool:
       status = getattr(exc, "status_code", None)
       return status is not None and 400 <= status < 500 and status != 429
   ```

3. **Log on retry.** `before_sleep=before_sleep_log(log, WARNING)` so
   silent retries don't hide a rate-limit problem during demo prep.

**Timeout settings on the Mistral client:** 30s for upload, 120s for
the OCR call (a 300-page book usually finishes well under that).

**Exception classes to retry:** placeholder — the exact `mistralai` SDK
exception types get refined once the package is in `uv.lock`. Expected
to follow standard HTTP semantics (`SDKError` or similar for 5xx/network).

---

## D13 — CLI shape: one full-pipeline command + a probe script ✅

```bash
uv run python -m src.ingest \
    Data/Books/grade4_science_sem1.pdf \
    --grade 4 \
    --subject science \
    --book "Science — Semester 1" \
    --book-id grade4_science_sem1
```

This is a thin wrapper over `ingest_book()` — argparse + call + print
the `IngestResult` as JSON.

**Validation built into the CLI (two layers):**

1. **At argparse:** `--grade` is restricted to `choices=[4, 7, 10]`.
   Any other value is rejected before any work starts.
2. **At runtime in `ingest_book()`:** before OCR, verify the Chroma
   collection for that grade actually exists. If not, raise a clear
   error pointing the user at `init_chroma.py`. Fails fast — no paid
   Mistral call burned discovering a missing collection later.

```python
def ingest_book(pdf_path, *, grade, subject, book, book_id) -> IngestResult:
    if not book_id:
        raise ValueError("book_id must be a non-empty string")

    try:
        get_collection(grade)
    except Exception as e:
        raise ValueError(
            f"No Chroma collection for grade {grade}. "
            f"Run: uv run python -m src.retrieval.init_chroma"
        ) from e

    if subject not in SUBJECTS:
        raise ValueError(f"subject must be one of {SUBJECTS}, got {subject!r}")
    ...
```

**Validation pass uses a separate one-off script**, not the main CLI:

```bash
uv run python scripts/ocr_probe.py \
    Data/Books/f0e95389-GE-PE-K05-SM1-TFML_first20.pdf \
    --pages 0-2 \
    --out data/processed/_probe/
```

The probe imports `ocr_book` directly, skips chunking + Chroma, and
just dumps `raw_ocr.json` for inspection. ~20 lines.

---

## D14 — Grade 5 sample disposition: validation-only ✅

The on-disk `f0e95389-GE-PE-K05-SM1-TFML_first20.pdf` is a Grade 5
sample. Pilot is grades 4, 7, 10 (BUILD_SPEC §5.1).

**Resolution:** the CLI's `--grade choices=[4, 7, 10]` makes ingesting
Grade 5 into Chroma impossible without modifying code. The K05 sample
exists purely for the OCR validation pass through `scripts/ocr_probe.py`,
which never writes to Chroma.

No `grade_5` collection added. Pilot scope stays clean. When real
Grade 4 / 7 / 10 PDFs arrive, ingestion is the same one-command flow.

---

## D15 — Markdown dumps for inspection and reuse ✅

After `raw_ocr.json` lands, `ocr_book()` also writes plain markdown
files. Pure side-effect — the chunker still reads `raw_ocr.json` as
the source of truth; the `.md` files are for humans and any future
non-chunking reuse (grepping, sharing, diffing OCR quality).

```
data/processed/grade_{N}/{subject}/{book_id}/
├── upload.json
├── raw_ocr.json
├── book.md                # full book concatenated, with page separators
└── pages/
    ├── p000.md
    ├── p001.md
    └── ...
```

**Content:** the *raw* per-page markdown from `OCRPageObject.markdown`
— pre-D7-cleanup. Image placeholders, headers, and footers stay
inline so you can see exactly what Mistral returned and spot OCR
errors. The cleaned text (image placeholders stripped per D7) lives
only inside the Chroma chunks, not on disk as `.md`.

**Page numbering in filenames:** zero-padded (`p000.md`, `p001.md`, …)
so shell listing sorts naturally. This differs from chunk IDs (D9:
`{book_id}__p0`, unpadded) — intentional: chunk IDs are never sorted
as files, but `ls pages/` is much nicer with padding.

**`book.md` layout:** pages concatenated with an HTML-comment
separator that records the source page index, so a reader can trace
any line back to a specific page:

```markdown
<!-- page 0 -->
{page-0 markdown}

<!-- page 1 -->
{page-1 markdown}
...
```

**Atomic writes** (D12 pattern): every `.md` file is written via
`.tmp` + rename. Prevents corrupt files if the process is killed
mid-write.

**Re-run behavior:** identical to D3 — if `raw_ocr.json` is a cache
hit, the `.md` files are regenerated from it (cheap, deterministic,
no API call). Force a fresh dump by deleting the cache directory.

**Why both per-page and combined:**
- `pages/pNNN.md` pairs 1:1 with chunks → easiest way to debug a
  retrieval hit ("why did chunk `p042` come back? open `p042.md`").
- `book.md` is what you grep or open in an editor for a whole-book
  view, or share with a collaborator who doesn't want to parse JSON.

---

## D16 — `--pages` flag on the main CLI for cheap end-to-end iteration ✅

The probe script (`scripts/ocr_probe.py`) supports `--pages 0-2` for
~$0.003 OCR-only runs, but the main CLI in D13 has no equivalent.
Without it, every full-pipeline test (OCR → chunk → embed → Chroma)
costs ~$0.25 and 1–2 min on a real book — too slow for iterating on
`chunk.py` / `load.py`.

**The flag:**

```bash
uv run python -m src.ingest grade4_science_sem1.pdf \
    --grade 4 --subject science \
    --book "Science — Semester 1" --book-id grade4_science_sem1 \
    --pages 0-10        # full pipeline, first 11 pages only
```

Same parser syntax as the probe — `0-10` parses to `pages=[0..10]` and
forwards through `ocr_book(..., pages=...)` (already wired per D5).

**Sibling cache directory so partial runs don't poison the full-book cache:**

```
data/processed/grade_4/science/grade4_science_sem1/             # full book
data/processed/grade_4/science/grade4_science_sem1+pages0-10/   # partial
```

When `--pages` is set, the cache dir gets a `+pages{lo}-{hi}` suffix.
A later full run still finds an empty primary cache dir and re-OCRs
the whole book (correct); the partial cache stays inspectable for
debugging.

**Where the suffix logic lives:** in `ingest_book()` (the orchestrator),
not in `ocr_book()`. The phase functions stay structure-agnostic —
`ocr_book` just receives a `cache_dir` Path already shaped correctly,
plus the `pages` it should forward to the Mistral call. Keeps each
phase pure and the suffix convention in one place.

**Chroma behavior during a partial run:** real chunks land in the
grade collection, but only for the requested pages. D10's
delete-then-add still keys on `book_id` (per the D8/D10 update
above), so a subsequent full run cleanly replaces the partial set
with the full set. Safe.

**Cost:** ~5 lines of argparse + a `_cache_subdir(pages)` helper.
Buys cheap end-to-end iteration during build-out, doesn't affect the
production "ingest a whole book" path.

---

## D17 — Logging during ingest: stage-by-stage INFO to stdout ✅

A full-book ingest takes 1–2 minutes; silence over that window is bad
UX and hides which stage failed. `ingest_book()` and its phase
functions use Python's `logging` module at INFO level, same
`basicConfig` style as `src/retrieval/init_chroma.py:25-29`.

**Required log lines (one per stage transition):**

```
14:23:01  INFO  uploading grade4_science_sem1.pdf (52 MB)
14:23:18  INFO  uploaded → file_id=abc123
14:23:18  INFO  OCR start (model=mistral-ocr-latest, pages=all)
14:24:42  INFO  OCR done — 287 pages
14:24:42  INFO  wrote raw_ocr.json (8.3 MB)
14:24:42  INFO  wrote 287 page .md files + book.md
14:24:42  INFO  chunking — 284 chunks (3 blank pages skipped)
14:24:43  INFO  embedding batch 1/18 …
14:25:31  INFO  loaded 284 chunks into grade_4
```

After the stage logs, the CLI still prints the `IngestResult` as JSON
on stdout (unchanged from D13) so scripts can parse the final output
without grepping log lines.

**Logger names:** `aleem.ingest` at the orchestrator, plus per-phase
sub-loggers `aleem.ingest.ocr`, `aleem.ingest.chunk`,
`aleem.ingest.load`. Lets you silence one phase during debug.

**Where `basicConfig` is called:** only in `src/ingest/__main__.py`
(the CLI entry point). Library code — `ingest_book()` and the phase
functions — never calls `basicConfig`, following the standard-library
HOWTO: applications configure logging, libraries don't. Programmatic
callers (tests, scripts, notebooks) configure their own logging. This
avoids the silent-no-op trap of calling `basicConfig` from
`ingest_book()` itself when another handler is already installed.

**Cache-hit logging:** when `raw_ocr.json` is a cache hit (D3), say so
explicitly — `"OCR cache hit (raw_ocr.json, pdf_sha256=…)"` — so a
re-run user knows they didn't just pay Mistral again.

**Retry logging (already in D12):** `before_sleep_log(log, WARNING)`
so silent retries surface on the same channel.

**This is separate from the L18 JSONL query log.** That file
(`logs/queries-YYYY-MM-DD.jsonl`) is for the query path; ingestion
logs go to stdout only.

---

## Validation plan (before going wide)

The Mistral context doc lists 5 questions to answer before committing
to the chunker design. The OpenAPI spec already resolves one (page
index is 0-based, confirmed). Four remain — answer them on the first
real OCR run:

1. ~~Page index 0 or 1?~~ → **0-based** (spec line 11892,
   re-confirmed: probe returned `index` values 0, 1, 2).
2. Heading hierarchy: does `#` consistently mean chapter, `##` mean lesson?
   **Inconclusive from probe** — pages 0–2 are the book cover and
   inner front matter. Mistral did emit a clean `#` / `##` / `###`
   ladder for the cover (`#` book title, `##` grade level, `###`
   "authored by"). Re-evaluate against a chapter spread during the
   Grade 8 ingest (`pages 0010-0020`) before committing to v2.
3. ~~Arabic reading order: logical RTL or visual order?~~ → **logical
   RTL (correct)**, verified empirically in the Mistral playground on
   an Arabic textbook sample before this plan was finalized.
   Programmatically re-confirmed against the probe — the cover page
   reads `المملكة العربية السعودية` etc. in logical order, no
   reversed glyph sequences.
4. Content-type headings: do "مثال" / "تمرين" / "تعريف" come through as
   actual `#`/`##` headings? **N/A in probe** (front matter has no
   exercises). Re-evaluate during Grade 8 ingest.
5. Orphan pages: do pages without a heading get grouped correctly under
   the previous heading? **N/A in probe**. Re-evaluate during Grade 8
   ingest.

**Bonus: image annotation works as designed.** With `annotate_images:
true` the response carries a populated `OCRImageObject.image_annotation`
on each detected figure (probe returned, e.g.,
`{"description": "An image of a QR code.", "kind": "QR code"}` for the
publisher's QR code on page 1). This is what D7 v1.1 inlines into chunk
text as `[Image: …]`.

**Probe:** `ocr_book(pdf_path, pages=[0,1,2])` on
`Data/Books/f0e95389-GE-PE-K05-SM1-TFML_first20.pdf`. Cost: ~$0.003.
Inspect `raw_ocr.json`, write findings here, then finalize the chunker.
Fixture for tests captured at `tests/fixtures/ocr_k05_p0-2.json`.

**Branch plan if results disagree with assumptions:**
- Q3 returns logical RTL (expected) → continue.
- Q3 returns visual-order or mixed → **STOP** before building
  `chunk.py`. Mistral OCR is unusable for retrieval at that point;
  fall back to a different OCR provider (Google DocAI, Azure AI
  Document Intelligence, or Qari — the original BUILD_SPEC §4.1
  choice). The empirical playground check above should already make
  this branch unreachable, but the programmatic re-confirmation is the
  hard gate.
- Q2 / Q4 / Q5: record the answer in this file but don't block v1 —
  they only inform v2 (lesson-based chunking + chapter/lesson_title
  metadata).

---

## Implementation order

Build in this sequence. Each step is small and verifiable before moving on.

1. **Plumbing** — add `mistralai` to `pyproject.toml`, `uv sync` to update
   the lockfile, add `MISTRAL_API_KEY=` to `.env.example`, set it in
   your local `.env`, and add `data/processed/` to `.gitignore` so
   cached OCR JSON + per-page markdown dumps never get committed.
2. **Config wiring (D11)** — add an `ingestion:` section to
   `config.yaml` with `embed_batch_size: 16`, and add an
   `IngestionConfig` dataclass to `src/config.py` mirroring the shape
   of `MemoryConfig`. Must land before step 8 (`load.py`) which
   imports `settings.ingestion.embed_batch_size` — without this step
   that import fails with `AttributeError` on first run.
3. **`src/ingest/types.py`** — `Chunk` and `IngestResult` dataclasses.
4. **`src/ingest/ocr.py`** — `ocr_book()` with caching (D3), Files API
   upload (D4), atomic writes + 4xx denylist + retry logging (D12),
   markdown dumps for inspection (D15).
5. **`scripts/ocr_probe.py`** — ~20-line helper that calls `ocr_book`
   with `pages=[0,1,2]` and dumps the JSON.
6. **Run the validation pass.** `ocr_probe.py` on the K05 sample.
   Inspect `raw_ocr.json`, answer the four open validation questions
   in this file's Validation Plan section.
7. **`src/ingest/chunk.py`** — `chunks_from_ocr()`. Page-based (D6),
   image-placeholder strip (D7), empty default metadata (D8),
   deterministic IDs (D9).
8. **`src/ingest/load.py`** — `load_chunks()`. Delete-then-add (D10),
   batched embedding (D11).
9. **`src/ingest/__init__.py`** — orchestrator `ingest_book()` with
   the upfront grade/subject/book_id validation (D13).
10. **`src/ingest/__main__.py`** — argparse CLI wrapper (D13).
11. **`src/ingest/README.md`** — per the project convention (every
    folder has one).
12. **Tests** — one test file per phase under `tests/`. Use a
    canned `OCRResponse` fixture so tests don't hit the API. Fixture
    is `tests/fixtures/ocr_k05_p0-2.json`, captured from step 6's
    validation pass output (first 3 pages, ~50 KB) and checked in.
    Realistic schema, zero extra effort to produce.
13. **First real ingest** — pick one Grade 4 / 7 / 10 PDF, run the
    full CLI, confirm chunks land in Chroma.
14. **Swap the retrieve stub** — change `src/graph/nodes/retrieve.py`
    from hardcoded chunks to real Chroma + Jina rerank.
15. **Doc updates** — apply the spec-drift updates listed below.

---

## Spec drift to update once v1 lands

These project docs still reference Qari and the old single-page
metadata schema:
- `BUILD_SPEC.md §3` (Stack table — OCR row)
- `BUILD_SPEC.md §4.1` (entire OCR section)
- `README.md` (Stack table)
- `src/ingest/README.md` (if/when created)

Don't update them now — wait until v1 is actually running on one book.
Then the doc updates reflect reality, not plans.
