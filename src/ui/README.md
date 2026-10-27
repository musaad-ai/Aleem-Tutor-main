# `src/ui/` — Chainlit application

The student-facing UI, wired to the tool-calling agent in
`src/graph/agent.py`. Streams the agent's tool calls as ephemeral
"Searching: …" cards, streams answer tokens as they're produced, then
runs the post-hoc verifier and renders citation source cards.

## Files

| File                          | What it does                                                                                                       |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `app.py`                      | Chainlit entrypoint — subject chat profiles, grade settings panel, context chip, auth stub, `@cl.on_chat_start` / `@cl.on_chat_resume` / `@cl.on_message`. |
| `persistence.py`              | Wraps Chainlit's `SQLAlchemyDataLayer` over `./.aleem/chats.db`. Exposes `make_data_layer()` and the pure `rebuild_history()` helper used on resume. |
| `schema.sql`                  | `CREATE TABLE IF NOT EXISTS` statements for the five tables (`users`, `threads`, `steps`, `elements`, `feedbacks`) the data layer queries. Applied once on launch. |
| `chainlit.md`                 | Markdown shown on the Chainlit landing screen (the welcome content).                                              |
| `.chainlit/config.toml`       | Chainlit config — app title, custom CSS, and the Aleem logo + default assistant avatar.                            |
| `public/stylesheet.css`       | Arabic font + per-message auto-RTL. See `public/README.md`.                                                        |
| `public/aleem-logo.svg` / `public/aleem-avatar.svg` | Header logo + assistant avatar, referenced from `config.toml`.                              |
| `public/elements/ContextChip.jsx` | `cl.CustomElement` rendering the pinned Grade·Subject chip (fixed-position pill).                              |

## Run

```bash
cd src/ui
PYTHONPATH=../.. uv run chainlit run app.py
```

Default URL: <http://localhost:8000>.

The Chroma collections must exist (`uv run python -m src.retrieval.init_chroma`
from the repo root) before launching. With `llm.backend: fake` in
`config.yaml` you don't need an API key — the agent returns a canned
answer with zero tool calls. Flip to `openrouter` (and set
`OPENROUTER_API_KEY` in `.env`) for real tool-calling generation.

## What you see per query

- **Ephemeral status line** — one Chainlit message that updates as the
  agent runs. Starts at "⏳ Thinking…", then becomes "🔎 Searching:
  '<verbatim query>'" for each retrieve call. Removed once the answer
  is rendered.
- **Token streaming** — the agent's final answer streams token-by-token
  into the live message.
- **Citation cards** — every `[n]` in the answer becomes an expandable
  `cl.Text` element on the side with chunk text + lesson title + page.
- **Refusal (tutor-voiced)** — when the verifier rejects or the agent
  hits the tool-call ceiling, the streamed text is replaced with a warm
  refusal that suggests related topics drawn from whatever chunks were
  retrieved.

## What you don't see

The L18 JSONL log captures the full post-hoc trace per query:
`tool_calls[]`, `verifier_verdict`, `citation_flags`, `refusal_reason`,
and `latency_ms{agent_loop, verifier, total}`. Tail it during demo runs:

```bash
tail -F logs/queries-$(date +%Y-%m-%d).jsonl | jq .
```

## Persistent chat history

Chat history is stored locally in `./.aleem/chats.db` (gitignored) via
Chainlit's official `SQLAlchemyDataLayer` over SQLite + `aiosqlite`.

Three hooks wire it together (all in `app.py`):

| Hook                       | What it does                                                                                                |
| -------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `@cl.data_layer`           | Returns the configured `SQLAlchemyDataLayer`. `persistence.make_data_layer()` applies `schema.sql` first.   |
| `@cl.header_auth_callback` | Local-only stub: always returns `cl.User(identifier="local")`. No login screen.                             |
| `@cl.on_chat_resume`       | Rebuilds `cl.user_session["history"]` from persisted messages; restores `grade` + `subject` from thread metadata. |

History is passed straight into the agent as a list of HumanMessage /
AIMessage objects (see `history_to_messages` in `src/graph/agent.py`),
so the agent resolves "quiz me on that" against prior turns without any
separate rewrite step.

**Wipe local history:** `rm -rf .aleem/`. The schema is recreated on the
next launch.

## How the UI ↔ agent plumbing works

The handler builds the prebuilt agent (`build_agent(grade, subject)`)
and subscribes to `agent.astream_events(state, version="v2")`:

| LangGraph event              | Chainlit primitive             | Notes                                                            |
| ---------------------------- | ------------------------------ | ---------------------------------------------------------------- |
| `on_tool_start` (name=retrieve) | status_msg → "🔎 Searching: …" | Surface the verbatim query the agent passed to retrieve.        |
| `on_chat_model_stream`       | `msg.stream_token(token)`      | Filtered by `metadata.langgraph_node == "agent"`.                |
| `on_chain_end` (name=LangGraph) | capture `data.output.messages` | Source for the final answer text + downstream finalize step.    |
| `GraphRecursionError` raised | trigger `ceiling_hit=True`     | Sized at `2 * max_tool_calls + 3` → refusal in agent's voice.    |
| any other exception          | `cl.Message("⚠️ Pipeline …")` | Surfaces persistent failures with a visible error card.          |

Once the stream closes, the handler calls `finalize_agent_run()` — the
same helper `run_agent()` uses — which runs `parse_citations()` and the
topical verifier, then returns the full state update including the
canonical final answer. The streamed message is replaced with that
canonical answer (so verifier-driven refusals overwrite whatever tokens
came through earlier).

Graph internals never import Chainlit. This file is the only seam.

## Model prewarm

`app.py` calls `prewarm_models_in_background()` at module import so the
Jina embedder + reranker load in a daemon thread while Chainlit is
spinning up. Without this, the first retrieve blocks the asyncio loop
for ~15s and the status bar can't update until the load completes.
The prewarm skips itself when every grade collection is empty — see
`src/retrieval/prewarm.py`.

## Grade & subject selection

Chainlit gives two selector surfaces; Aleem maps one to each axis:

- **Subject → chat profile** (the top dropdown). One profile per subject;
  the profile name maps back to an internal `key` via
  `_profile_name_to_key()`. Picking a different subject starts a new chat.
- **Grade → ⚙ settings panel** (a `Select`). Grade is the sticky "user
  setting": a new chat defaults to the grade of the user's most recent
  thread (`_last_used_grade()`, read from thread metadata), so it rarely
  needs re-picking. Changing it mid-chat updates the pinned context chip.

Both are persisted to thread metadata at `on_chat_start` and restored on
`on_chat_resume`. The agent's tool reads grade + subject from a
`contextvars.ContextVar` set by `set_request_context()` at the top of
`on_message`, so the LLM-facing `retrieve(query)` signature stays narrow.

Subject keys: `islamic_studies`, `social_studies`, `english`,
`digital_skills`. Grades: `4`, `7`, `8`, `10`.

## Not here

- Agent + tools + verifier definitions — `src/graph/agent.py`,
  `src/graph/tools.py`, `src/graph/verifier.py`.
- LLM client + retry — `src/graph/client.py`.
- Prompt templates — `prompts/agent.j2`, `prompts/verifier.j2`.
- Query logging — `src/graph/logging.py`.
