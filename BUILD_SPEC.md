# Aleem — Build Spec

Locked design decisions from the grilling session. Build to this; revisit only with cause.

---

## 1. Deliverable Shape

- **Primary deliverable:** Live demo (graded). Recording + slides produced afterwards.
- **Scope:** Demo-first; benchmark phase deferred until after the live demo.
- **Timeline:** 2 weeks, team of 2.

---

## 2. Architecture

```
User request + grade + subject (Chainlit ChatProfile selector)
  ↓
Conversation memory: rewrite the message into a standalone question
  using recent chat history ("quiz me on that" → "quiz me on photosynthesis")
  ↓
Query decomposition agent (split compound requests)
  ↓ for each sub-request:
    Intent classifier → one of {Q&A, explain, summarize, revise, quiz}
    ↓
    Embed query with Jina-v4 (retrieval.query mode)
    ↓
    Chroma top-20 from the user's grade collection (filtered by subject)
    ↓
    Jina Reranker v2 (jina-reranker-v2-base-multilingual) → top-5
    ↓
    ALLaM-7B self-check: "is the answer in these chunks? yes/no"
       ├─ no  → refusal + parent lesson titles of top-3 chunks
       └─ yes → ALLaM grounded generation with inline [n] citations
  ↓
Merge sub-answers → Chainlit renders with expandable source cards
```

---

## 3. Stack

| Layer | Choice | Notes |
| --- | --- | --- |
| OCR | `mistral-ocr-latest` (Mistral hosted) | Files-API upload + signed URL. Image annotation via `bbox_annotation_format`. See `OCR_implementation.md`. |
| Embedding | `jinaai/jina-embeddings-v4` | Use `retrieval.query` vs `retrieval.passage` task modes. |
| Vector store | Chroma (persistent local) | One collection per grade: `grade_4`, `grade_7`, `grade_8`, `grade_10`. |
| Reranker | `jinaai/jina-reranker-v2-base-multilingual` | Lives at `src/retrieval/reranker.py`; lazy-loaded cross-encoder. |
| Generator | `humain-ai/ALLaM-7B-Instruct-preview` | Quantize to 4-bit if VRAM is tight. Also used for self-check and intent classifier. |
| UI | Chainlit | Native source elements, streaming, `ChatProfile` for grade+subject. Custom RTL Arabic CSS. |
| Orchestration | LangGraph | Pipeline has a per-task loop + a self-check branch + shared state — a graph shape, not a linear LCEL chain. Same ecosystem as the proposal's LangChain. |

---

## 4. Pipeline Details (Locked)

### 4.1 OCR — see `OCR_implementation.md`
The Qari plan was replaced by Mistral OCR (`mistral-ocr-latest`) once the
project lost its OCR collaborator. Full design + decisions (D1–D17) live
in `OCR_implementation.md`; this section is the headline only.
- Upload via Mistral Files API, then `client.ocr.process(...)` against
  the returned signed URL.
- Image annotation **on by default** (`config.yaml :: ingestion.annotate_images`)
  so diagrams become retrievable via inline `[Image: <description>]`
  substitutions in chunk text.
- Per-page caching keyed by PDF SHA-256 — re-running `ingest_book()` on
  the same PDF/book_id is free.

### 4.2 Chunking — v1 is page-based
Detail in `OCR_implementation.md` §D6–D9.
- **v1: one chunk per non-blank OCR'd page.**
- Each chunk metadata:
  `{grade, subject, book, book_id, chapter, lesson_title, page, content_type}`.
  `chapter` and `lesson_title` default to empty strings until the v2 chunker.
- `content_type ∈ {lesson_body, example, exercise, definition}` — fixed
  at `lesson_body` in v1 (the LLM post-pass that would tag the others
  remains v2 work).
- v2 (lesson-aware chunking with chapter/lesson_title extraction) is a
  single-file swap of `src/ingest/chunk.py` when we get there.

### 4.3 Retrieval
- Dense-only with Jina-v4. **No BM25, no hybrid.**
- `top-20 retrieval → Jina Reranker v2 (jina-reranker-v2-base-multilingual) → top-5 → LLM`.
- Grade isolation is structural (separate collections per grade), not policy-based.
- Subject is a metadata filter at query time.

### 4.4 Generation
- Generator: ALLaM-7B-Instruct-preview.
- Grounding-strict system prompt requires:
  - Answer only from retrieved chunks.
  - Inline `[n]` citation markers per claim.
  - Exact refusal phrase if context is insufficient: `لم أجد هذا في الكتاب المدرسي` / `I couldn't find this in your textbook`.
  - Respond in the user's query language.
- Format retries on JSON/citation breakage **deferred** — implement only if §6.2 faithfulness eval shows real-world citation breakage (out-of-range `[n]`, missing citations, malformed brackets). Until then the pipeline trusts the model's output and parses it as-is.

### 4.5 Refusal Path
- **LLM self-check only** (no score threshold). ALLaM is asked "do these chunks contain the answer? yes/no" before generation.
- On refusal: return canonical phrase + the **parent lesson titles** of the top-3 retrieved chunks, framed as "Related topics in your textbook". No additional retrieval; reuse already-retrieved chunks.
- Instrument: log top-k scores + self-check verdict for every query so a threshold can be added later if data warrants.

### 4.6 Agentic Layer
- **Conversation memory (query rewrite).** Recent chat history is kept per session. Before decomposition, one cheap LLM call rewrites the incoming message into a self-contained question using that history (e.g., "quiz me on that" → "quiz me on photosynthesis"). Everything downstream always receives a standalone question, so no other node needs history awareness.
  - Implemented as a cuttable prefix node — if week 2 is tight, disable it and the pipeline still works on fully-specified questions.
  - "Session" therefore means app settings (grade/subject) **plus** recent chat turns.
- **Query decomposition agent** at the top of the pipeline. Splits compound requests (e.g., "explain X and quiz me") into atomic sub-requests. Each sub-request flows through the intent classifier + RAG independently; results merged in the final response.
  - Always runs as a node; output is always a list of tasks — `[query]` for a single-task request, `[task1, task2]` for a compound one. A config flag can force `[query]` without an LLM call (the cut-it escape hatch for Risk #4).
- **No retry/reformulation loop** in this iteration.

### 4.7 Intent Classifier
- Few-shot prompt mapping each request to one of: Q&A, explain, summarize, revise, quiz.
- Implemented as an ALLaM call (or a small fast model) with structured output.
- Each mode has its own generation prompt template; all share the same retrieval layer and grounding contract.
- `teacher-support` was previously listed and has been dropped: no defined behavior, no eval coverage, and any plausible interpretation either needs content sources outside the textbook chunks or breaks the grounding contract. Listed as post-demo roadmap.

### 4.8 UI
- Chainlit with:
  - `ChatProfile` for grade + subject selection at session start (state held in session/localStorage; no auth).
  - RTL Arabic layout — proper mirroring, not just `direction: rtl`. Arabic-friendly font (IBM Plex Sans Arabic / Cairo).
  - Streaming responses.
  - **Lesson-level citations**, inline numbered `[n]`, with expandable source cards showing the exact retrieved passage.

---

## 5. Scope

### 5.1 Grades
Pilot grades: **4, 7, 8, 10**. The original demo targeted one grade per
stage (4 / 7 / 10), but grade 8 was added alongside the OCR pipeline so
step 13 of `OCR_implementation.md` had a real PDF to ingest. The two
intermediate-school grades (7 and 8) now share a stage.

### 5.2 Subjects
Five per grade: **Arabic, Islamic studies, social studies, English, Math**.
- Math is conditional on Qari OCR validation. If math-page equation transcription is unusable, drop math and fall back to 4 text-heavy subjects.

### 5.3 Out of Scope (for this demo)
- Diagram understanding (skipped; multimodal Jina-v4 is a stretch).
- Equation rendering beyond text-OCR (no LaTeX-typeset display).
- User authentication / profiles.
- Production multi-tenancy.

---

## 6. Evaluation

### 6.1 Demo-Phase Smoke Set
- **60 questions total.**
- 70% authored from end-of-chapter exercises with gold answer + source page.
- 20% **should-refuse** off-curriculum questions to validate refusal works.
- 10% adversarial bilingual (Arabic question about English textbook content and vice versa).
- One teammate authors, the other reviews.

### 6.2 Metrics for Demo Phase
- **Retrieval:** Recall@5 — did the gold source chunk appear in the top-5 reranked results?
- **Refusal:** Precision and recall on the should-refuse subset.
- **Faithfulness:** Manual eyeball on ~20 generated answers — do all claims trace to retrieved chunks?
- Full RAGAS metrics deferred to benchmark phase.

### 6.3 Benchmark Phase (Post-Demo)
- 150–300 question gold set, same format, published as a HuggingFace dataset.
- Embedding bake-off (Jina-v4 vs BGE-M3 vs Multilingual-E5 vs Arabic-Triplet-Matryoshka).
- ALLaM vs Claude/Gemini generator comparison.
- RAGAS faithfulness, answer correctness, context precision.

---

## 7. Compute & Deployment

- **OCR, chunking, embedding** all run offline, once. Outputs persisted to the repo (Chroma DB file checked in or attached release).
- **At query time** only Jina-v4 (query embedding), Jina Reranker v2, and ALLaM-7B run live.
- **Primary inference:** cloud GPU (Modal / Runpod / Lambda — to be finalized).
- **Chainlit UI:** local laptop during the demo.
- **Backup for demo day:** pre-tested cloud GPU instance with the full stack, in case the local/primary path fails.

---

## 8. Top Risks (Watch List)

1. **Qari quality on math pages.** Drives the math go/no-go decision on day 3–4. Validate early.
2. **ALLaM grounding discipline under thin context.** 7B models hallucinate confidently. Mitigated by strict prompt + self-check + format retries.
3. **Live demo robustness.** Network/GPU failure on demo day. Cloud GPU fallback is mandatory, not optional.
4. **Decomposition agent eating week 2.** Additive feature, not on the critical path. Cut without hesitation if base Q&A isn't solid by day 9.
5. **RTL Arabic UI polish in Chainlit.** Not first-class; budget time for CSS overrides.

---

## 9. Open Decisions (Not Yet Locked)

- Exact cloud GPU provider (Modal vs Runpod vs Lambda).
- Quantization scheme for ALLaM if VRAM-bound (4-bit AWQ / GPTQ / bitsandbytes).
- Math content_type tagging strategy if equations survive OCR.

---

## 10. Task List

Active task tracking for the agentic / query-path layer has moved to `RESPONSE_WORKFLOW.md` (decisions L1–L18). The ingestion half (OCR → chunking → embedding → Chroma population) is owned by the collaborator and tracked separately.

---

*Spec frozen post-grilling. Changes require a new grill round.*
