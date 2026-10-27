# `src/graph/` — tool-calling agent

The agentic half of Aleem. Replaces the earlier two-graph LangGraph
pipeline (rewrite → decompose → map(intent → retrieve → self_check →
(generate | refuse) → citations) → merge) with **one tool-calling agent
+ one tool + two post-hoc safety layers**.

Spec: `docs/WORKFLOW_SANDBOX.md` (§1–§13). Merged from `workflow-sandbox`
in commit `f20595a`; `RESPONSE_WORKFLOW.md` (L1–L22) is the historical
baseline.

## Files

| File | What it does |
| ---- | ------------ |
| `state.py`    | `Chunk` (mirrors the Chroma metadata schema), `Citation`, `HistoryTurn`, `ToolCallRecord`, the unified `AgentState` TypedDict, and `initial_agent_state()`. No more `TaskState` / `OuterState`. |
| `client.py`   | `get_llm(temperature=..., for_agent=False)` + `get_verifier_llm()`. The agent path uses `for_agent=True` to skip the L20 retry wrap (`RunnableRetry` breaks `create_react_agent`'s `bind_tools` check). |
| `tools.py`    | `@tool retrieve(query)` — the agent's only tool. Wraps Chroma top-20 → Jina rerank top-5, plus the empty-collection stub fallback. Per-request `grade`/`subject` flow through `contextvars` so the LLM-facing tool signature stays narrow. |
| `prompts.py`  | Jinja2 template loader. `render(name, **vars)` for system-only prompts (used by `agent.py`), `render_pair(name, **vars)` for `\n---\n`-split (system, user) pairs (used by `verifier.py`). |
| `parse.py`    | `parse_citations(answer, chunks) -> (Citations, flags)` — §3 layer 2 structural check. Pure function. Flags: `out_of_range:[n]`, `no_citations`. |
| `verifier.py` | `verify_topical(question, answer, chunks) -> VerifierDecision` — §4 layer 3 topical check via `with_structured_output(VerifierDecision)`. Fails open on infra errors so the verifier can't break the pipeline. |
| `agent.py`    | `build_agent(grade, subject, llm=None)` + `run_agent(state)` + `finalize_agent_run(...)`. `create_react_agent` from `langgraph.prebuilt` + ceiling enforcement via `recursion_limit = 2 * max_tool_calls + 3`. |
| `logging.py`  | `@timed("phase")` + `log_query(state)` for the new JSONL schema (`tool_calls`, `verifier_verdict`, `citation_flags`, `refusal_reason`, `latency_ms{agent_loop, verifier, total}`). |

## How to run end-to-end

```bash
# Fake-backend run — no API key. Returns a canned answer with zero tool
# calls (the fake model doesn't emit tool_call AIMessages by default).
uv run python scripts/smoke_run.py "what is photosynthesis?" --grade 7 --subject islamic_studies

# Real run — set OPENROUTER_API_KEY in .env and flip
#   llm.backend: openrouter
# in config.yaml. The agent will actually call retrieve(), receive chunks,
# write inline [n] citations, and pass the verifier.
uv run python scripts/smoke_run.py "what is photosynthesis?"
```

Programmatic invocation:

```python
import asyncio
from src.graph.agent import run_agent
from src.graph.state import initial_agent_state

state = initial_agent_state(
    grade=7, subject="islamic_studies",
    user_query="what is photosynthesis?",
)
result = asyncio.run(run_agent(state))
print(result["final_answer"])
print(len(result["tool_calls"]), "tool calls")
print(result["verifier_verdict"])
```

## Key design points

- **One LLMClient, one config switch (L3, preserved).** `client.py` keeps
  the swappable-backend pattern. Set `for_agent=True` for the agent path
  to skip the retry wrap that breaks `create_react_agent`.
- **Tool call budget = 4 (§3).** Hard ceiling, enforced via LangGraph's
  `recursion_limit`. Ceiling-hit and verifier-rejection produce the same
  §3 refusal shape (agent's voice + topic suggestions from already-
  retrieved chunks).
- **Gradient signal, not a binary gate (§4).** Rerank scores are injected
  into the agent's context (`[n] (relevance: 0.94) …`), replacing L6's
  yes/no self-check. The agent judges contextually; the verifier is the
  actual backstop.
- **Refusal is prompt-driven + verifier-checked (§3, supersedes L1).**
  No canonical phrase. Warmth comes from suggestions; trust signal lives
  in the L18 verifier verdict, not in the wording.
- **Citations are still inline (L5, preserved).** The agent writes `[n]`
  per claim; `parse.py` maps them to `Citation` objects. Out-of-range or
  missing markers are flagged structurally, never repaired.
- **Verifier fails open.** If the structured-output call crashes (e.g.
  malformed JSON from the model), the verifier returns `on_topic=True`
  with the error in the `reason` field. Better to ship an answer than
  block on infrastructure.

## Not here

- Chroma client + embedder + reranker — see `src/retrieval/`. `tools.py`
  is just the LangGraph wiring on top.
- Prompts — see `prompts/README.md` (`agent.j2`, `verifier.j2`).
- The Chainlit UI — see `src/ui/app.py`. It uses `build_agent` +
  `finalize_agent_run` directly so it can stream tool calls and tokens.
