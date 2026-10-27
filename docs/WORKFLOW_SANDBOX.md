# Workflow Sandbox — Agent-Loop Spec

**Branch:** `workflow-sandbox`
**Status:** Locked after grilling. Ready to build.
**Owner:** Turki
**Scope:** Replace the inner+outer LangGraph pipeline in `src/graph/` with a single tool-calling agent. The sandbox lives only on this branch. If it beats `main` on the eval set, merge and delete the old pipeline. If not, the branch dies.

This document is self-contained — a new chat should be able to read this and build the system without needing the prior grilling transcript.

---

## 1. Why this exists

Two UX symptoms drove the change:

1. **Rigid intent routing.** Every task is forced into one of `{qa, explain, summarize, revise, quiz, chat}`. Real questions blend modes ("explain photosynthesis and quiz me on it"); the classifier picks one lane and the answer fits that lane.
2. **Cold robotic refusals.** L1's canonical phrase + lesson-title redirect is safe but doesn't sound like a tutor.

Both share the same root cause: **the graph models the LLM as a sequence of deterministic stages when the LLM is capable of making most of those decisions itself.**

A third symptom — too many sequential LLM calls before the first token — exists but is not the justification for this change. Latency improvements (or regressions) are a side effect, tracked but not the goal.

---

## 2. Current shape (what we are replacing)

Full detail in `RESPONSE_WORKFLOW.md`.

```
  Outer graph: rewrite → decompose → map_tasks → merge → END
  Inner graph (per task): intent → (chat | retrieve) → self_check → (generate | refuse) → citations → END
```

Five named stages, two graphs, six intents, one hard refusal gate.

---

## 3. Target shape

One tool-calling agent + one tool + two post-hoc safety layers.

```
                  Student message + full chat history
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │  Agent  (one LLM, looped)                   │ ◄────────────────┐
        │                                             │                  │
        │  System prompt:                             │                  │
        │   • Aleem tutor over Saudi MoE textbooks    │   tool result:   │
        │   • Cite every factual claim as [n]         │   chunks +       │
        │   • Only state facts found in chunks        │   relevance      │
        │   • When you call retrieve, construct a     │   scores         │
        │     search query that works for semantic    │                  │
        │     search — use topic terms, not the       │                  │
        │     student's exact phrasing. Resolve       │                  │
        │     pronouns / references from history      │                  │
        │     first. Try Arabic↔English terms if      │                  │
        │     the first attempt scores low.           │                  │
        │   • Read the relevance scores; if chunks    │                  │
        │     look weak, refine your query or refuse  │                  │
        │   • Match the student's language            │                  │
        │   • If you cannot answer from chunks,       │                  │
        │     refuse in your own voice and suggest    │                  │
        │     related topics the chunks do cover      │                  │
        │                                             │                  │
        │  Tool: retrieve(query)                      │                  │
        │  Budget: max 4 tool calls per turn          │                  │
        └─────────────────────────────────────────────┘                  │
                              │                                          │
            ┌─────────────────┴─────────────────┐                        │
            │                                   │                        │
            ▼                                   ▼                        │
    tool_call: retrieve(...) ──► Chroma top-20 → rerank top-5            │
    (0 to 4 calls — agent's choice)                                      │
                              │                                          │
              chunks injected as: [1] (relevance: 0.94) ... ─────────────┘
                              │
                              ▼
              Streamed answer with inline [n] markers
                              │
                              ▼
   ┌──────────────────────────────────────────────────┐
   │  Layer 2: Citation parse (free, structural)      │
   │   • [n] out of range? → flag                     │
   │   • Factual claims with no [n]? → flag           │
   └──────────────────────────────────────────────────┘
                              │
                              ▼
   ┌──────────────────────────────────────────────────┐
   │  Layer 3: Topical verifier (default-on)          │
   │   one cheap LLM call on a small fast model       │
   │   "Is this answer about a topic the chunks       │
   │    actually discuss, or is it off-textbook?"     │
   └──────────────────────────────────────────────────┘
                              │
       ┌──────────────────────┴──────────────────────┐
       │ on-topic                                    │ off-topic
       ▼                                             ▼
   Render + citation cards                  Refuse in agent's voice +
                                            topic suggestions from
                                            already-retrieved chunks
                              │
                              ▼
            L18 JSONL log: tool calls, citation flags,
            verifier verdict, rerank scores, latency
```

### Tool-call ceiling behavior

If the agent hits the 4-call budget without producing an answer, it refuses using the same shape as a verifier-rejected refusal — agent's voice + topic suggestions from whatever chunks have been retrieved. "Ran out of searches" and "answer was off-topic" produce the same student-facing experience.

---

## 4. Safety model — defense in depth

| Layer | Mechanism | Cost | Catches |
| ----- | --------- | ---- | ------- |
| 1 | System-prompt grounding contract + cite-every-claim rule + relevance-score awareness | free | most cases — agent follows instructions |
| 2 | `[n]` citation parse (structural) | free | missing / out-of-range citations |
| 3 | Topical verifier on a small fast model (default-on) | 1 cheap LLM call | catastrophic topic drift — answer is about something the chunks don't cover |

**Gradient signal:** rerank scores are injected into the agent's context (e.g. `[1] (relevance: 0.94) ...`), so the agent sees chunk quality and can re-query or refuse. This replaces L6's binary self-check gate with a richer signal. No fixed threshold — the agent judges in context (reranker scores are not on a universal scale, and the verifier is the actual backstop).

**Why a topical verifier and not a per-claim one?** A per-claim check ("walk every factual claim, confirm it's in a chunk") is fragile — it flags paraphrases and reasonable inferences, fires the repair loop constantly, and adds latency for marginal accuracy gain. The topical check catches the failure mode that actually destroys tutor trust (totally wrong subject) and accepts the chloroplasts-vs-mitochondria edge as residual risk. If logs show that residual is too high, upgrade to per-claim later — but build the simpler thing first.

**Why no retry loop on verifier rejection?** For a topical verifier, rejection usually means either the agent ignored the prompt or the chunks didn't cover the question. Retrying on the same chunks with the same model produces the same result. Refuse immediately, save the LLM call.

**Why no pre-generation chunk-relevance gate?** The agent already reads the chunks. If they are irrelevant its natural moves are to re-query or refuse. Adding a pre-generation relevance gate would re-import the procedural rigidity we are trying to remove.

---

## 5. What this revisits in `RESPONSE_WORKFLOW.md`

Multiple locked L decisions are reopened. Each one is now resolved:

| L# | Current decision | What the sandbox does |
| -- | ---------------- | --------------------- |
| L1 | Refusal is structurally guaranteed (no free-generate path) | Refusal becomes prompt-driven + verifier-checked, in the agent's own voice. No canonical phrase requirement. |
| (rewrite node) | Dedicated stage rewrites the student's message into a clean retrieval query | Gone as a node. Agent constructs the retrieve query itself at tool-call time, with feedback — if scores are low it refines and calls again (0–4 calls). Better than the old one-shot rewrite because the agent sees the chunks come back. |
| L4 | `decompose()` always runs | Gone. Compound queries handled as multiple tool calls in one agent context. |
| L6 | Strict yes/no self-check gate | Gone as a node. Rerank scores injected as gradient signal; topical verifier handles topic drift. |
| L8 / L9 | Two LangGraph graphs, per-task inner loop | Replaced with `create_react_agent`. Still LangGraph, different shape. |
| L11 | `cl.Step` per stage shows the pipeline | Replaced with visible tool calls. Search queries shown verbatim. |
| L12 / L22 | Six fixed intents + chat-vs-educational split | Gone. Agent picks tone naturally; chat vs. educational becomes "did the agent call retrieve?". |
| L13 | Intent classifier via structured output | Gone — no intent node. |
| L16 | Language detection via rewrite | Moved to the agent prompt: "match student's language." |

---

## 6. What stays unchanged

- **L2 / Chunk contract** — `retrieve(query, grade, subject)` still returns the same `list[Chunk]`. Wrapped as a `@tool`.
- **L3 / `LLMClient`** — same swappable backend; agent uses the same client for generation. Verifier uses a separately configured small fast model through the same client interface.
- **L5 / `[n]` inline citations** — agent emits them per prompt instruction; parsed downstream.
- **L14 / no citation retry** — kept. Parse and render what the model produced.
- **L17 / `config.yaml`** — extended, structure unchanged.
- **L18 / JSONL logging** — records tool calls + verifier verdicts now.
- **L19 / Jinja prompts** — agent prompt + verifier prompt live in `prompts/`.
- **L20 / retry as Runnable wrapper** — unchanged.
- **L21 / Chainlit `astream_events`** — tool calls become the ephemeral steps.

---

## 7. Implementation guidance (non-negotiable)

Use LangGraph / LangChain primitives. Do **not** prompt-engineer structure when a framework primitive exists.

| Use this | Not this |
| -------- | -------- |
| `langgraph.prebuilt.create_react_agent` | Hand-rolled Thought/Action/Observation parsing loop |
| `@tool` decorator + `llm.bind_tools([retrieve])` | "Call retrieve like this: RETRIEVE(...)" with string parsing |
| `llm.with_structured_output(VerifierDecision)` with Pydantic | "Return JSON in this format" prompt |
| `SystemMessage` / `HumanMessage` / `AIMessage` / `ToolMessage` lists | String-concatenated prompts |
| `Runnable.with_retry(...)` (L20) | Custom retry shim |
| `astream_events(version="v2")` (L21) | Custom event loop |

House style on `main` already follows this (L13, L16, L20, L21). The sandbox stays in the same style.

---

## 8. File layout

The sandbox lives on the `workflow-sandbox` branch and **modifies `src/graph/` directly**. No `graph_v2/` split — the branch itself is the separation. `main` retains the old pipeline; the branch contains the new one. If eval passes, merge replaces the old with the new in one move.

```
src/graph/
  agent.py          # create_react_agent + binding (replaces old graph builders)
  tools.py          # @tool retrieve()
  verifier.py       # with_structured_output topical verifier
  parse.py          # [n] parse + structural flag detection

prompts/
  agent.j2          # tutor role + grounding rules + citation format + refusal style
  verifier.j2       # topical check prompt
```

### `config.yaml` additions

```yaml
agent:
  max_tool_calls: 4              # hard ceiling on retrieve calls per turn

verifier:
  enabled: true                  # topical verifier on by default
  model: <small-fast-model-id>   # e.g. claude-haiku-4-5; separate from agent's model
```

No `use_agent_loop` flag (the branch is the toggle). No `keep_canonical_refusal` (no canonical phrase to enforce). No `rerank_low_threshold` (agent judges scores in context). No `memory.max_turns` override for the agent — full session history is passed raw (see §11).

---

## 9. Build plan

One commit per locked sub-decision, matching `main`'s commit style.

1. `tools.py` — wrap `retrieve()` as a `@tool` returning `[Chunk]` with rerank scores in metadata.
2. `prompts/agent.j2` — tutor role, citation contract, score-aware behavior, retrieve-query construction guidance (resolve references from history, use topic terms not student phrasing, retry with translated terms on weak scores), agent-voiced refusal style with topic-suggestion guidance.
3. `agent.py` — `create_react_agent(llm, tools, state_modifier=system_prompt)` with the existing `LLMClient`. Enforce `max_tool_calls: 4`; on ceiling-hit produce the refusal shape from §3.
4. `parse.py` — `[n]` extraction + structural flag detection.
5. `src/ui/app.py` — wire the agent path; surface tool calls as ephemeral updates with the **verbatim query string** in the trace (e.g. "Searching: photosynthesis chloroplasts").
6. `tests/test_agent.py` — smoke test on `backend: fake` (no API key needed).
7. `verifier.py` + `prompts/verifier.j2` — topical check via `with_structured_output(VerifierDecision)`, configured to call the small fast model.
8. Wire verifier into agent path. On rejection → refuse immediately (no retry). Log verdict to L18.

---

## 10. Resolved decisions reference

These were open questions in the proposal; all are now locked. Recorded here so a future reader knows the rationale.

1. **Refusal shape.** Agent's own voice + topic suggestions from already-retrieved chunks. No canonical phrase. Warmth comes from suggestions; trust signal comes from the verifier verdict in the log, not from the wording.
2. **Tool-call budget.** 4 calls. On ceiling-hit, refuse using the §3 refusal shape.
3. **Rerank threshold.** Removed. Scores injected into context, agent judges contextually. Verifier is the actual backstop.
4. **Sequential vs parallel tool calls.** Let the model decide via `bind_tools` — modern tool-calling models emit parallel calls when sensible. Not a design knob.
5. **Memory window.** Full session, raw, no summarizer, no `max_turns` cap. Soft cap only as a safety net for runaway sessions (e.g. >50 turns).
6. **Demo trace.** Verbatim search queries. Paraphrase layer can be added later if a polished demo needs it.
7. **Verifier model.** Small, fast, separately configured. Topical check is well within the reach of a small model.
8. **Repair-loop retry budget.** Zero. Refuse immediately on verifier rejection.
9. **Kill criteria.** See §12.
10. **Canonical phrase enforcement.** Removed entirely — nothing to enforce.

---

## 11. Tradeoffs to keep in mind

- **L1's hardness weakens.** Was "impossible by construction"; now "prompt + topical verifier." Acceptable only if eval shows hallucination rate stays below the floor in §12. Without that eval, do not merge.
- **Topical verifier accepts subtle hallucinations as residual risk.** The chloroplasts-vs-mitochondria case (right topic, wrong specifics) is not caught. If L18 logs show this happening often, upgrade to a per-claim verifier — but only then.
- **No summarizer.** Full session history is passed raw to the agent every turn. If long-session context bloat or agent-distraction shows up in logs, the absence of a summarizer is the place to look.
- **No rerank threshold.** The agent could in principle answer from low-relevance chunks. The verifier is meant to catch that downstream; if it doesn't, this is where to add a guardrail.
- **Demo trace looks different.** Old: five named stages visible. New: tool calls + answer with verbatim queries. Arguably more interesting; a demo grader will notice.
- **All-or-nothing surface change.** This collapses L4/L6/L9/L12/L13/L16/L22 together. Reverting one piece in isolation means reverting the whole branch.

---

## 12. Eval and kill criteria

The sandbox is judged on three signals against an eval set. Latency is reported but does not gate the decision.

**Hard floors (cross any → kill):**
- **Hallucination rate** — answer contains a claim not supported by the cited chunks. Target threshold to be calibrated on the eval set once it exists.
- **Refusal accuracy** — when the question is genuinely out-of-textbook, the system must refuse. Target threshold to be calibrated.

**Positive bar (must beat `main` to merge):**
- **Rigidity score on blended queries.** A small held-out set of deliberately blended queries ("explain X and quiz me on it", "summarize then ask me about it"). Grade whether the answer addresses both modes. The sandbox must score higher than `main` here. This is the reason the sandbox exists; if it doesn't move, the change is not worth its risk.

**Tracked but not gated:**
- Latency (p50, p95).
- Tool-call distribution per turn.
- Verifier rejection rate.

The eval set itself is **not in scope for this branch** — see §13. Building it is a prerequisite for the merge decision, but separate work.

---

## 13. Not in scope

- **Ingestion** (`src/ingest/`) — collaborator owned, untouched.
- **Eval / benchmark harness** (BUILD_SPEC §6) — must exist before the merge decision, but is separate work. This spec assumes the eval set will be built before §12's thresholds are calibrated and the merge call is made.
- **Persistent chat sessions** — already shipped on `main`, used as-is.
- **New backends or model swaps** — `LLMClient` boundary unchanged. The small verifier model uses the same client interface, just configured to a different model id.
- **Per-claim hallucination verifier** — explicitly deferred. Only build if topical verifier proves insufficient on eval data.
