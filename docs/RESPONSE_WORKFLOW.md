# Response Workflow — Agentic + Generation Layer

> **Historical (pre-merge) document.** The two-graph pipeline described in
> L1–L22 below was superseded when `workflow-sandbox` merged into `main`
> (commit `f20595a`). The shipping shape is a single tool-calling agent
> per `docs/WORKFLOW_SANDBOX.md` (§3 diagram, §4 safety model, §12 eval bar).
> This file is preserved as the design history for the L-decisions and as
> the comparison baseline that motivated the new shape.

Working notes for the LLM/agentic half of Aleem (decomposition → intent → self-check → generation → citations).
Owner: Turki. Embedding/retrieval layer: collaborator.

This doc tracks decisions as we grill them. Locked items build to; open items are still being argued.

---

## Scope of this branch

In scope (Turki):
- `retrieve()` — query embed → Chroma top-20 → Jina rerank top-5 (the full query path)
- Query decomposition agent
- Intent classifier
- ALLaM self-check (refusal gate)
- Grounded generation per intent
- Citation assembly
- Sub-answer merge

Out of scope (collaborator): ingestion only — parse books → MD → chunk → embed → populate Chroma.

The two halves meet at the **Chunk contract** (see L2).

---

## Locked decisions

### L1 — Refusal path does NOT free-generate
When self-check returns "no", the response is:
1. Canonical phrase: `لم أجد هذا في الكتاب المدرسي` / `I couldn't find this in your textbook`
2. Parent lesson titles of the top-3 retrieved chunks, framed as "Related topics in your textbook"

No ungrounded ALLaM answer. The diagram's "try to answer it on your own" is rejected — it breaks the §4.4 grounding contract and risks confident hallucination to students. Matches BUILD_SPEC §4.5.

---

### L2 — Turki owns the full query path, including rerank
`retrieve(query, grade, subject) -> list[Chunk]` does query embed → Chroma top-20 →
Jina reranker → top-5. Collaborator's scope stays at ingestion only. Build against a
**stub** first (hardcoded chunks) so the agentic pipeline runs while Chroma is empty.

**Chunk contract** — the metadata schema is already locked in
`src/retrieval/chroma_client.py` lines 8-18:
`{grade, subject, book, chapter, lesson_title, page, content_type}`.
`lesson_title` is present → refusal path (L1) is safe.

---

## Project state (snapshot 2026-05-15)

Exists and **built**:
- `src/retrieval/` — chroma_client, Jina-v4 embeddings, init_chroma.
- `src/ui/app.py` — Chainlit wired to `outer_graph.astream_events` per L21.
- `src/graph/` — full LangGraph pipeline (state, client, prompts, logging,
  inner, outer + 9 nodes: rewrite, decompose, intent, retrieve, self_check,
  generate, refuse, citations, chat).
- `prompts/*.j2` — 6 Jinja templates (one per LLM-using node).
- `config.yaml` — backend selector + feature flags per L17.
- `scripts/smoke_run.py` — CLI smoke test, no API key needed in `backend: fake`.
- `tests/` — pytest suite (61 tests, one per locked decision; backend forced
  to `fake` by autouse fixture).
- `logs/queries-YYYY-MM-DD.jsonl` — written per-query by L18 logging.

Stubbed (waiting on collaborator scope or follow-up):
- `src/graph/nodes/retrieve.py` returns 3 hardcoded photosynthesis chunks per
  L2 until ingestion populates Chroma. Metadata schema matches
  `src/retrieval/chroma_client.py:8-18` so the swap is a single-file change.
- `src/ingest/` — OCR + chunking, collaborator-owned, not yet built.

---

### L3 — All LLM calls go through one swappable client, configured by file
Every node (decomposition, intent, self-check, generation) calls a single
`LLMClient.complete(system, user, ...)` interface — never `transformers` directly.

- **Dev backend:** OpenRouter free models.
- **Deployment backend:** ALLaM-7B (cloud GPU) — decided later, swap with no code change.
- Model id, base url, api key, temperature etc. live in a **config file** (not hardcoded,
  not scattered). One place to change the backend.

---

### L4 — `decompose()` always runs, always returns a list of tasks
One LLM call at the top of the pipeline. Output is always `list[Task]`:
- single-task query → `[query]` (the default case)
- compound query ("summarize and quiz me") → `[task1, task2]`

Downstream always loops over the list via `run_one()` (intent → retrieve → self-check
→ generate → cite), then `merge()` combines results (trivial passthrough when len==1).

`run_one()` is the critical path — built and hardened first. `decompose()` + `merge()`
are a thin wrapper. A **config flag** can force `decompose()` to return `[query]`
without calling the LLM — the cut-it escape hatch for §8 risk #4.

---

### L5 — Citations are produced inline by ALLaM, only rendered downstream
Chunks are handed to ALLaM pre-numbered (`[1] ...`, `[2] ...`). ALLaM writes the
`[n]` markers inline per claim as it generates. The downstream "citations" node does
**not** invent any mapping — it parses the `[n]` markers ALLaM already wrote and
renders the matching sources as expandable cards **at the bottom** of the response.

Format retries (§4.4): if ALLaM emits an out-of-range `[n]` or no citations, re-prompt.
**Recommended fallback (not yet confirmed by Turki):** after 2 failed retries, show the
answer with a small "sources couldn't be verified" note rather than refusing.

---

### L6 — Self-check uses the strict bar, and only gates Q&A / explain
One ALLaM call over all 5 reranked chunks together. The question is strict:
*"Based only on these chunks, can the student's question be fully answered? yes/no."*
Not the loose "is this relevant" bar — loose lets ALLaM fill gaps by inventing.

Self-check only gates **Q&A** and **explain** intents. For **summarize / quiz / revise**,
if retrieval returned chunks at all, proceed straight to generation (no "answer" to
check for). Per §4.5: log the verdict + the 5 reranker scores for every query.

---

### L7 — Chat memory via query-rewrite (standalone-question) prefix
Keep the last few turns of conversation. Before `decompose()`, one cheap LLM call
rewrites the new user message into a self-contained question using the history
("quiz me on that" → "quiz me on photosynthesis").

Everything downstream (decompose, intent, retrieve, ...) is unchanged — it always
receives a standalone question. Built **last**, after `run_one()` and decomposition.
Cuttable prefix node if week 2 is tight.

This is the real meaning of "Extract session + user query" in the diagram: session
= app settings (grade/subject) **plus** recent chat history.

---

### L8 — Orchestration is LangGraph
The pipeline is a graph: a per-task loop, a self-check branch (yes → generate,
no → refuse), and shared state across nodes. LangGraph fits box-for-box; plain LCEL
LangChain would fight the branch. Spec §3 updated from "LangChain" to "LangGraph".

---

### L9 — Two graphs, shared state, debug dict
**Outer graph:** rewrite-question → decompose → (map inner graph over tasks) → merge.
Outer state holds `list[TaskState]` + final merged answer.

**Inner graph** (`run_one`, over `TaskState`): intent → retrieve → self-check → generate
→ resolve-citations. Retrieve runs per task (subtasks can be different topics).
Decompose output per task is a clean topical question — intent verb not baked in.

`TaskState` fields: `grade, subject, standalone_question` (inputs); `intent, chunks,
self_check_passed, answer, citations, refused` (filled by nodes); `debug: dict`
(reranker scores + self-check verdict per §4.5, dumped once at the end).

---

### L10 — Module layout and branch
Branch: `agentic-pipeline`. Code lives under `src/graph/`:

```
src/graph/state.py        TaskState, OuterState, Chunk
src/graph/client.py       LLMClient — OpenRouter backend (L3)
src/graph/nodes/*.py      one file per node (rewrite, decompose, intent,
                          retrieve, self_check, generate, refuse, citations)
src/graph/inner.py        inner graph (run_one)
src/graph/outer.py        outer graph (decompose / map / merge)
src/config.py             loads config.yaml
config.yaml               model id, base url, feature flags (in-repo)
prompts/                  prompt templates, one per node, out of code
```
Secrets via `.env`. Day-one: all nodes are stubs, `retrieve()` returns hardcoded
chunks, graph runs end-to-end without an API key.

---

### L11 — Stream the pipeline, not just the final answer
Self-check gates generation, so the first answer token is ~2s away. Instead of
hiding that as a spinner, use Chainlit `cl.Step` to surface each stage:
"Understanding your question...", "Finding relevant pages...", "Checking the
textbook...", then stream answer tokens normally in the generate node.

Reframes latency as "showing work" — and the agentic pipeline becomes visible
to the demo grader (decomposition, retrieval, self-check all on screen).

---

### L12 — `teacher-support` intent dropped
Five educational intents: Q&A, explain, summarize, revise, quiz. `teacher-support`
removed from BUILD_SPEC §2 and §4.7 — no defined behavior, no eval coverage,
no available content source for pedagogical advice that wouldn't break grounding.
Logged in spec as post-demo roadmap.

Affects:
- `TaskState.intent` literal type → 5 values for educational paths.
- Intent classifier prompt → 5 categories (smaller, more reliable).

> **Updated by L22**: a 6th intent `chat` was later added for non-curriculum
> conversational messages (greetings, small talk, meta-questions). It does
> NOT participate in the grounding contract — it routes around retrieve and
> self_check entirely. See L22.

---

### L13 — Intent classifier uses LangChain structured output
The intent node calls `llm.with_structured_output(IntentDecision)` where:

```python
class IntentDecision(BaseModel):
    intent: Literal["qa", "explain", "summarize", "revise", "quiz"]
```

The `Literal` constraint plus the underlying provider's function-calling / JSON
mode means the return is a typed `IntentDecision` — format hallucination
impossible. (Semantic mistakes — picking the wrong intent — are still possible.)

**Implication for `config.yaml`:** the chosen OpenRouter dev model must support
function calling / tool use. "Free + supports tools" — Llama-3.1-8B-Instruct,
Gemini Flash, Mistral all qualify; the very cheapest text-only models do not.
List this as a hard requirement when picking the dev model.

Scope: locked for the **intent node** specifically. Whether decompose and
self-check also switch from defensive-parse to structured output is a separate
decision (likely yes later — same pattern, different schemas — but not locked now).

---

### L14 — Citation retries deferred; trust the model for now
No retry loop on `[n]` citation breakage. Parse what the model produced and render
it. If eval (§6.2 faithfulness eyeball) shows real-world citation failures —
out-of-range `[n]`, missing citations, malformed brackets — revisit and add the
one-retry-then-refuse loop (option C). Until then, demo-first: don't build
infrastructure for a hypothetical failure.

This narrows L5: the "Format retries" promise in BUILD_SPEC §4.4 is **deferred to
post-eval**, not gone.

---

### L15 — Subtasks run sequentially
When decompose returns >1 task, the outer graph iterates with a plain `for` loop —
each task's inner graph finishes before the next starts. Reasons:
- Compound queries are the minority (~5%); the savings apply rarely.
- Sequential streaming (L11) tells a clean linear story per task — parallel would
  show interleaved `cl.Step` updates that confuse a watching grader.
- Avoids OpenRouter free-tier rate-limit risk on demo day.
- Upgrade to parallel is one-line later if needed: swap loop for LangGraph `Send`.

---

### L16 — Language detected by piggybacking on the rewrite step
The rewrite-history node (L7) already runs an LLM on the query. Use structured
output to return both fields in one call — no extra LLM call:

```python
class StandaloneQuery(BaseModel):
    standalone_question: str
    language: Literal["ar", "en"]
```

`TaskState.language` is read only by the **refuse** node to pick the canonical
phrase (`لم أجد هذا في الكتاب المدرسي` vs `I couldn't find this in your textbook`).
Generate, decompose, rewrite all handle language via prompt instructions
("respond in the user's language") — no explicit dispatch needed.

**Fallback if L7 is cut:** regex `r"[؀-ۿ]"` on the raw query. Same
state key, downstream unchanged.

---

### L17 — `config.yaml` shape: backend-pluggable, in-repo, secrets in `.env`

One backend at a time — `backend: openrouter` or `backend: ollama`. Same backend
serves classifier and generation nodes (no per-node mixing). Per-node temperature
applies on top: `classifier_temperature: 0.0`, `generation_temperature: 0.6`.
Reranker model is in config for symmetry with retrieval knobs.

```yaml
# config.yaml — Aleem agentic-pipeline configuration.
# Secrets (API keys) live in .env, NOT here.

llm:
  backend: openrouter   # openrouter | ollama  (one at a time)

  openrouter:
    # NOTE: model MUST support function/tool calling for structured outputs
    # (L13 intent classifier, L16 rewrite+language). Free options that qualify:
    #   meta-llama/llama-3.1-8b-instruct:free
    #   google/gemini-2.0-flash-exp:free
    #   mistralai/mistral-7b-instruct:free
    model: meta-llama/llama-3.1-8b-instruct:free
    base_url: https://openrouter.ai/api/v1
    # api key read from OPENROUTER_API_KEY in .env

  ollama:
    # NOTE: model MUST support tool calling — llama3.1, qwen2.5, mistral-nemo
    # all work; gemma2 does not. Pick later when local target is decided.
    model: llama3.1:8b
    base_url: http://localhost:11434

  classifier_temperature: 0.0   # intent, self-check, decompose, rewrite
  generation_temperature: 0.6   # final answer
  max_tokens: 1024
  timeout_seconds: 30

# Cuttable nodes (BUILD_SPEC Risk #4)
features:
  history_rewrite_enabled: true   # L7 — regex fallback for language if false
  decomposition_enabled: true     # L4 — force [query] if false (no LLM call)

# Retrieval (Turki's retrieve() per L2 + collaborator's reranker seam)
retrieval:
  top_k_retrieve: 20
  top_k_rerank: 5
  reranker_model: jinaai/jina-reranker-v2-base-multilingual  # spec §9 — v2/v3 TBC

# History window for L7 rewrite node
memory:
  max_turns: 4
```

`.env` needs `OPENROUTER_API_KEY=` added (not in current `.env.example`).

---

### L18 — Logging: JSONL file + one-line stdout summary

Per BUILD_SPEC §4.5, every query writes a structured record:
```json
{"ts": "...", "session_id": "...", "query": "...", "grade": 7,
 "subject": "...", "language": "ar", "intent": "explain",
 "rerank_scores": [0.94, 0.81, 0.73, 0.67, 0.59],
 "self_check_passed": true, "refused": false,
 "latency_ms": {"rewrite": 480, "decompose": 510, ...}}
```

- Source of truth: `logs/queries-YYYY-MM-DD.jsonl` (gitignored, daily rotation
  so demo-day file is identifiable).
- Stdout: one-line summary per query for dev visibility:
  `[18:31:02] grade=7 ar intent=explain top1=0.94 ✓check 1240ms`
- Skip literalai/Chainlit tracing for demo — nice-to-have, doesn't aggregate
  for the §4.5 threshold question.

The JSONL is what feeds the post-demo decision in §4.5 ("add a score threshold if
data warrants") and the §6.3 benchmark phase.

---

### L19 — Prompt templates use Jinja2, one file per node
Prompts live in `prompts/*.j2` (per L10, out of code). Jinja gives real
templating (`{% for chunk in chunks %}[{{ loop.index }}] ...{% endfor %}`)
which is the natural fit for L5's pre-numbered chunk block in the generate
prompt. Files are pure text — non-coders can iterate on prompts without
touching Python.

Considered: `.txt + str.format` (forces pre-rendering the chunk list in
Python, so the prompt file no longer tells the whole story) and Python
string constants (clutters modules, breaks L10).

Convention: templates use `\n---\n` on its own line to separate the system
prompt from the user message; `src/graph/prompts.render_pair(name, ...)`
splits on it and returns a `(system, user)` tuple. Adds `jinja2` to
`pyproject.toml` (already a transitive of `chainlit`).

---

### L20 — LLMClient retries transient errors, surfaces persistent ones
The `LLMClient.complete()` boundary (L3) is wrapped with `tenacity` via
LangChain's idiomatic `Runnable.with_retry(...)`:
`stop_after_attempt=3`, `wait_exponential_jitter=True`,
`retry_if_exception_type` for the transient OpenAI classes
(`APIConnectionError`, `APITimeoutError`, `InternalServerError`,
`RateLimitError`). 4xx (auth / quota / bad request) and exhausted retries
raise — caught at the Chainlit `@cl.on_message` handler and rendered as a
visible error card.

One failure-handling pattern at one boundary keeps nodes pure. Real
failures surface in the L18 JSONL log instead of hiding behind per-node
fallbacks (which would make silent degradation indistinguishable from a
successful run — the worst interpretability bug).

---

### L21 — Chainlit wires the outer graph via async + `astream_events`
`@cl.on_message` stays async (Chainlit-native) and invokes the compiled
outer graph with `outer_graph.astream_events(state, version="v2")`.
LangGraph emits a structured event per node start/end → mapped to
`cl.Step` cards in the handler (one per `app.NODE_LABELS` key). Token
streaming for the generate node flows through the same event stream
(`on_chat_model_stream`, filtered by `metadata.langgraph_node == "generate"`).
Final state is captured from the root chain's `on_chain_end` and used to
render `cl.Text` citation cards.

Nodes themselves stay pure (no `cl.*` calls inside graph code) — the same
graph runs identically in `scripts/smoke_run.py`, in tests, and in any
future non-UI caller. This is the wiring that makes L11 ("stream the
pipeline, not just the final answer") work — the agentic stages become
visible to the demo grader without coupling the graph to the UI layer.

> **Iterated 2026-05-15**: the per-node `cl.Step` cards proved cluttered
> in the live demo. Replaced with a single ephemeral `cl.Message` whose
> content updates on each node start and is removed once the answer is
> rendered (ChatGPT / Claude.ai pattern). Same `astream_events` plumbing;
> only the rendering primitive changed. The full per-node trace lives in
> the L18 JSONL log.

---

### L22 — `chat` intent for non-curriculum messages
A 6th intent `chat` covers greetings, thanks, small talk, meta-questions
("what did I just ask?"), and "what can you do?" type asks. L1's strict
refusal makes sense for textbook questions but felt cold for everyday
conversation. L22 evolves L12 (which dropped `teacher-support` for being
ungroundable) — `chat` is groundable through a different mechanism: a
bounded prompt that forbids factual answers and always redirects to the
textbook.

Pipeline shape (inner graph):

```
intent  ──┬── chat → chat_node → END                ←  L22
          └── retrieve → self_check → (generate | refuse) → citations
                                                       ↑ L1 (refusal) governs this branch only
```

Implementation:
- `state.py`: `Intent` literal gains `"chat"` (now 6 values).
- `prompts/chat.j2`: must NOT answer factual questions, must stay in the
  student's language, ≤2 sentences, no `[n]` markers, end with a textbook
  invite.
- `nodes/chat.py`: streams via `llm.astream()` (same token-event hook
  the Chainlit handler uses for `generate`). Bypasses retrieve / self_check
  / citations entirely.
- `nodes/intent.py`: structured-output classifier extended to 6 values
  plus a `_looks_like_chat` heuristic (English word-boundary regex +
  Arabic substring) used **only** as fallback when the LLM classifier
  returns malformed tool-call output (the Llama-3.3-70B/vLLM hiccup we
  saw in live testing).
- `TaskState.history`: new field so `chat_node` can reference recent turns
  ("you just said hi"). L7's "downstream nodes see only the standalone
  question" contract still holds — educational nodes ignore `history`.

The grounding contract from L1 is **untouched** for educational intents.
Hallucination risk for the chat path is bounded by the prompt: no facts,
only social glue.

---

## Diagram vs. spec — known gaps to resolve

- [x] Refusal path — RESOLVED (L1)
- [x] Citations as post-gen step → RESOLVED (L5): inline `[n]` during generation, downstream only renders
- [x] "Merge sub-answers" missing from diagram → RESOLVED (L9): outer graph merge node
- [x] Query-language detection / "respond in user's query language" → RESOLVED (L16): language detected by L7 rewrite via structured output; regex fallback when L7 cut
- [x] Format retries → RESOLVED (L5): live in the citations-resolve node
