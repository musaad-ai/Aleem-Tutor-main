# Setup

How to get Aleem running on your machine from a fresh clone.

**Docker + `just` is the primary, supported path** — one image, same in
dev and deploy. The bare-metal `uv` flow (§6) is kept as a fast-restart
fallback for heavy local iteration, but everything below assumes Docker
unless a section says otherwise.

> **Current build status:** The query path is **one tool-calling agent +
> topical verifier** per `docs/WORKFLOW_SANDBOX.md`.
> `src/graph/agent.py::run_agent` drives a `create_react_agent` whose
> only tool is `retrieve(query)`; the agent writes inline `[n]`
> citations, a parser flags structural issues, and a small
> structured-output LLM call checks topical relevance.
> The only stub left is `retrieve()`'s fallback — it returns 3 hardcoded
> photosynthesis chunks when the per-grade Chroma collection is empty
> (the test default). So with `backend: fake` (the shipped default) the
> whole agent runs end-to-end with **no API key** (canned reply, no tool
> calls); with `backend: openrouter` you get real tool-calling generation
> grounded in those 3 stub chunks until ingestion populates Chroma.
>
> `RESPONSE_WORKFLOW.md` (L1–L22) is preserved as the historical
> comparison baseline (`docs/WORKFLOW_SANDBOX.md` §12).

---

## 1. Prerequisites

| Requirement      | Version / Notes                                                            |
| ---------------- | -------------------------------------------------------------------------- |
| **Docker**       | Docker Desktop, or `brew install colima docker docker-compose`. Must support Compose v2.30+ (`docker compose …`, `env_file: format: raw`). |
| **just**         | `brew install just` (macOS) or see <https://github.com/casey/just>. Thin task-runner over `docker compose` — every command below goes through it. |
| **git**          | Any recent version.                                                        |
| **OpenRouter**   | Optional. Needed only for real LLM answers — see §5. `backend: fake` (the default) works without it. |
| **Disk**         | ~5 GB free — torch + the Jina-v4 / reranker model caches dominate.         |

That's the whole list for the Docker path. Python, `uv`, and the
dependency tree all live **inside the image** — you do not install them
on the host.

> **Bare-metal extras (§6 only):** if you skip Docker and run via `uv`,
> you'll also want **uv** (`brew install uv`) and **~5 GB disk** for the
> local `.venv`. `uv` manages CPython 3.11 for you (pinned in
> `.python-version`); 3.12+ is not tested. **HuggingFace** (`HF_TOKEN`)
> and **Mistral** (`MISTRAL_API_KEY`) keys are only needed when the
> collaborator-owned ingestion pipeline runs — the query path never
> touches them.

---

## 2. Clone the repo

```bash
git clone <repo-url> Aleem
cd Aleem
```

---

## 3. Configure environment variables

```bash
cp .env.example .env
# Generate the required Chainlit session secret and paste it into .env:
uv run chainlit create-secret      # or, with no uv on the host: openssl rand -hex 32
```

Open `.env` and set whichever keys you need. All are optional for the
default `backend: fake` flow **except** the Chainlit secret:

| Var                    | When required                                                  | What it does                                                          |
| ---------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------- |
| `CHAINLIT_AUTH_SECRET` | Always (the UI registers an auth callback)                     | Signs the session cookie. Generate with `openssl rand -hex 32`.       |
| `OPENROUTER_API_KEY`   | Only when `llm.backend: openrouter`                            | Real LLM calls. Get a key at <https://openrouter.ai/keys>.            |
| `HF_TOKEN`             | Embeddings / reranker model download (gated Jina-v4)           | Accept the license on the HF model page first.                        |
| `MISTRAL_API_KEY`      | Only when running the ingest pipeline (`just ingest`)          | Powers the Mistral OCR call. Key at <https://console.mistral.ai/api-keys/>. |
| `CHROMA_DIR`           | No (Docker sets `/app/chroma`; bare-metal defaults to `./chroma`) | Override where Chroma persists its SQLite + parquet files.         |

> The container reads `.env` with `format: raw` (no `$`-interpolation),
> so a `CHAINLIT_AUTH_SECRET` containing literal `$` passes through
> untouched. This needs Compose v2.30+.

---

## 4. Run with Docker (the main path)

```bash
just build      # first build ~3–5 min (torch + transformers); cached after
just init       # create the per-grade Chroma collections (one-time)
just up          # → http://localhost:8000
```

Open <http://localhost:8000>. Pick a grade card, then optionally change
subject in the ⚙ settings panel, then ask any question.

Everything else is a `just` recipe (run `just` with no args to list them):

```bash
just logs        # tail chainlit + agent logs
just restart     # bounce after a config change
just shell       # interactive shell in the running container
just down        # stop + remove (bind mounts and the hf-cache volume survive)
just init        # (re)initialise Chroma collections — idempotent
just ingest      # run the Mistral OCR → embed pipeline (pass-through args)
just test        # run pytest inside the container (pass-through args)
just lint        # run ruff inside the container
just dev         # bare-metal fallback — runs Chainlit via local .venv (see §6)
```

### Verify the install

```bash
just test        # ~65 tests, all fake-backend, ~4s
just lint        # ruff check src/ tests/ scripts/
```

Both should report green.

### What you see per query (UI)

- A single status line at the top of the answer that **updates in place**
  as the agent runs: starts at `⏳ Thinking…`, becomes
  `🔎 Searching: "<verbatim query>"` for each retrieve call, and
  **disappears** once the final answer renders (ChatGPT / Claude.ai
  pattern).
- Real answers stream token-by-token from the agent's chat-model node.
- Citation cards (`[1]`, `[2]`, …) expand to show the source chunk's
  text, lesson title, and page.
- Off-topic answers and ceiling-hit refusals replace the streamed text
  with a warm, tutor-voiced refusal that suggests related topics drawn
  from whatever chunks were retrieved (`docs/WORKFLOW_SANDBOX.md` §3).
- Greetings / small talk: the agent simply doesn't call retrieve and
  produces a short conversational reply.

The full per-turn trace — tool calls, citation flags, verifier verdict,
latency breakdown — lives in `logs/queries-$(date +%F).jsonl`
(bind-mounted to the host). `just logs` tails the container; the JSONL is
the structured record.

### What gets baked vs. mounted

- **Baked into the image** so `docker run aleem:dev` works out of the
  box: `src/`, `prompts/`, `config.yaml`, `chroma/`, `Data/`.
- **Bind-mounted from the host** by `docker-compose.yml` so dev edits
  take effect live: `src/`, `tests/`, `scripts/`, `prompts/`,
  `config.yaml`, `pyproject.toml`, plus the stateful `chroma/`, `Data/`,
  `.aleem/` (chat history), and `logs/`.
- **Named volume** (`hf-cache`) holds the ~3 GB Jina-v4 + reranker
  weights so they survive image rebuilds.

In a prod deploy you'd drop the live-code overlays and rely on the baked
copies, or swap `chroma/` for a network-mounted volume.

### Persistent chat history

Past chats appear in the left sidebar and survive browser refresh. State
lives in `./.aleem/chats.db` (SQLite, gitignored, bind-mounted). The
schema is applied on first launch by `src/ui/persistence.py`. To wipe
local history: `rm -rf .aleem/`. The current build uses a single
hardcoded local user (`identifier = "local"`) — there's no login screen;
real auth is deferred to deploy.

### Building from Apple Silicon for an amd64 cloud target

```bash
docker buildx build --platform linux/amd64 -t aleem:amd64 .
```

(Skip this for local dev — `just build` targets the host arch.)

---

## 5. Choose your LLM backend (`config.yaml`)

`config.yaml` lives at the repo root and is bind-mounted into the
container, so an edit + `just restart` is enough — no rebuild. The base
shape is locked in `RESPONSE_WORKFLOW.md` L17; the `agent:` and
`verifier:` blocks are locked in `docs/WORKFLOW_SANDBOX.md` §8.

```yaml
llm:
  backend: fake          # fake | openrouter | ollama (one at a time)

  openrouter:
    model: meta-llama/llama-3.3-70b-instruct
    base_url: https://openrouter.ai/api/v1
    # api key read from OPENROUTER_API_KEY in .env

agent:
  max_tool_calls: 4      # §3 ceiling on retrieve() calls per turn

verifier:
  enabled: true          # §4 — default-on topical-relevance check
  model: ""              # empty → reuse llm.openrouter.model
                         # set to a smaller / faster model id when available
```

### `backend: fake`  *(default, no API key needed)*

`get_llm(for_agent=True)` returns a tool-aware `FakeListChatModel`
subclass whose canned reply has no tool calls — the agent loop
terminates after one LLM call, producing a stub answer with zero
retrieves. The verifier also short-circuits to `on_topic=True`. Great
for UI work, demos, and the test suite. Test code that wants to
exercise the full tool-call loop queues its own `AIMessage(tool_calls=…)`
via `FakeMessagesListChatModel` — see `tests/test_agent.py`.

### `backend: openrouter` *(real LLM)*

1. Add `OPENROUTER_API_KEY=…` to `.env`.
2. Flip `backend: openrouter` in `config.yaml`, then `just restart`.
3. About the model:
   - `meta-llama/llama-3.3-70b-instruct` (no `:free`) → paid route,
     ~$0.001/query. Reliable, doesn't 429. **Recommended.**
   - `meta-llama/llama-3.3-70b-instruct:free` → free route via 3rd-party
     upstream provider; heavily rate-limited. Expect HTTP 429 retries on
     bursty pipelines like Aleem (5 LLM calls per query).
   - Smaller free models (`nvidia/nemotron-nano-9b-v2:free`,
     `openai/gpt-oss-20b:free`) are less throttled but weaker at
     structured output.
4. The model **must** support tool-calling (the agent binds the `retrieve`
   tool via `bind_tools`). Verify against the live list at
   <https://openrouter.ai/api/v1/models> — the free roster rotates.

### `backend: ollama`

Placeholder slot in `config.yaml`. The factory raises `NotImplementedError`
until a local target is decided.

---

## 6. Bare-metal alternative (`uv`) — secondary

Skip Docker entirely when you want sub-second restarts during heavy
iteration. This path runs everything against a project-local `.venv`.
`just dev` wraps the Chainlit launch; the rest is plain `uv`.

### Prerequisites

- **uv** — `brew install uv`, or
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
  (Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`).
  Verify with `uv --version` (0.11.x+).

### Install dependencies

All deps are locked in `uv.lock` (committed) with exact versions and
wheel hashes. `uv sync` installs into `.venv/`, byte-identical for
everyone on the team and CI.

```bash
uv sync                     # runtime + dev deps (pytest, ruff)
```

First run takes ~2 min (torch is the heavy item); subsequent runs are
near-instant.

#### Pip fallback (no uv)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` is auto-generated from `uv.lock` — do not hand-edit.
Regenerate after a dep bump:

```bash
uv export --format requirements-txt --no-hashes --no-emit-project \
  --output-file requirements.txt
```

### Initialise Chroma + verify

```bash
uv run python -m src.retrieval.init_chroma          # create collections (idempotent)
uv run python scripts/smoke_run.py "what is photosynthesis?"   # agent end-to-end, no network
uv run pytest                                       # full suite
uv run ruff check src/ tests/ scripts/              # lint
```

The smoke run prints the canned stub answer, the (empty) tool-call list,
the verifier verdict, and the latency dict. With `backend: openrouter`
it drives the real agent and prints the verbatim search queries it made.

### Run the Chainlit UI

```bash
just dev
# equivalently, by hand:
(cd src/ui && PYTHONPATH=../.. uv run chainlit run app.py)
```

Open <http://localhost:8000>. Chainlit must be launched from `src/ui/`
so it picks up `.chainlit/config.toml` (see `src/ui/app.py:18`).

---

## 7. Daily commands cheat-sheet

```bash
# --- Docker (primary) ---
just build       # build / rebuild the image
just up          # start in background → :8000
just down        # stop + remove (volumes survive)
just restart     # bounce after a config.yaml edit
just logs        # tail container logs
just shell       # shell inside the container
just init        # (re)initialise Chroma collections
just ingest      # Mistral OCR → embed pipeline (e.g. just ingest --help)
just test        # pytest in container (e.g. just test -k retrieval)
just lint        # ruff in container

# --- Bare-metal (uv) ---
just dev                                             # Chainlit via local .venv
uv sync                                              # install / sync deps
uv add <package>                                     # add a dep (updates uv.lock)
uv run python -m src.retrieval.init_chroma           # (re)init Chroma
uv run python scripts/smoke_run.py "your question"   # smoke-run, no UI
uv run pytest                                        # full suite
uv run ruff check src/ tests/ scripts/               # lint

# --- Either path ---
tail -f logs/queries-$(date +%F).jsonl               # tail today's query log
```

---

## 8. Troubleshooting

### Docker path

**`just: command not found`**
`brew install just` (or see <https://github.com/casey/just>).

**Compose rejects `env_file: format: raw` / `path:`**
Your Docker is older than Compose v2.30. Upgrade Docker Desktop, or on
colima `brew upgrade docker docker-compose`.

**`CHAINLIT_AUTH_SECRET` errors at startup**
You didn't fill `.env`. Generate one (`openssl rand -hex 32`), paste it
in, then `just restart`.

**HF model download repeats on every rebuild**
The `hf-cache` named volume isn't being reused — check you're starting
via `just up` / `docker compose` (which mounts it), not a bare
`docker run` without `-v hf-cache:/cache/huggingface`.

### Backend / LLM

**HTTP 429 / "rate-limited upstream" from OpenRouter**
You're on a `:free` model and the upstream provider is throttling. Switch
to the paid route (drop `:free`; ~$0.001/query, requires credits), or to
a smaller free model (`nvidia/nemotron-nano-9b-v2:free`,
`openai/gpt-oss-20b:free`).

**HTTP 404 / "No endpoints found for `<model>:free`"**
That free endpoint was retired. Refresh the model id against
<https://openrouter.ai/api/v1/models> and pick a currently-live one.

**`Structured Output response does not have a 'parsed' field`** *(verifier path)*
A provider hiccup — empty structured-output payload. The verifier catches
this in `src/graph/verifier.py` and falls back to `on_topic=True` with the
error type in `reason`, so the answer still ships. If it's frequent, set
`verifier.model` to a more reliable model id in `config.yaml`.

**HuggingFace 401 / 403 when downloading Jina-v4** *(ingestion only)*
You haven't accepted the model license. Visit
<https://huggingface.co/jinaai/jina-embeddings-v4>, click **Agree**, and
make sure `HF_TOKEN` matches that account.

### Bare-metal (`uv`) path

**`chainlit: command not found`**
Forgot `uv run`, or didn't activate the venv. Use `just dev`, or
`uv run chainlit run app.py`, or `source .venv/bin/activate` first.

**`ModuleNotFoundError: No module named 'src'`**
Forgot `PYTHONPATH=../..` when running Chainlit from `src/ui/`, or you're
not at the repo root when running `uv run python -m src.retrieval.init_chroma`.
`just dev` sets this for you.

**`uv: command not found`**
uv isn't on PATH. Re-run the installer (§6) or restart your shell.

**Chainlit drops a stray `chainlit.md` or `.chainlit/` at the repo root**
You launched from the repo root instead of `src/ui/`. Stop the server,
delete the stray files, then `cd src/ui` before launching. `just dev`
already cd's correctly.

**`pip install` fails on `torch`**
On Apple Silicon, `torch==2.7.1` resolves to a universal wheel. On older
Linux, add `--extra-index-url https://download.pytorch.org/whl/cpu` to
force the CPU build.

**Chroma "missing embedding function on `get_collection`"**
Don't call `client.get_collection(...)` directly. Use
`src.retrieval.chroma_client.get_collection(grade)` — it re-attaches
Jina-v4 each open, which Chroma can't persist.

---

## 9. Where to go next

| If you want to…                       | Open                                       |
| ------------------------------------- | ------------------------------------------ |
| Understand the project pitch          | `README.md`                                |
| Understand the locked design          | `BUILD_SPEC.md` (§1–§10)                   |
| Understand the historical agentic-layer decisions | `docs/RESPONSE_WORKFLOW.md` (L1–L22) |
| Understand the current agent shape    | `docs/WORKFLOW_SANDBOX.md`                 |
| Navigate the codebase (for Claude)    | `CLAUDE.md`                                |
| Work inside the retrieval layer       | `src/retrieval/README.md`                  |
| Work inside the agentic graph         | `src/graph/README.md`                      |
| Work inside the Chainlit UI           | `src/ui/README.md`                         |
| Add or read tests                     | `tests/README.md`                          |
| Edit a prompt                         | `prompts/README.md` + `prompts/*.j2`       |
| See what's in `chroma/`               | `chroma/README.md`                         |
</content>
</invoke>
