# Aleem

Aleem is a curriculum-grounded RAG tutor over official Saudi Ministry of Education textbooks. Every answer must be traceable to a specific grade-level textbook chunk; refusal is a first-class outcome.

Code lives under `src/` (retrieval, graph orchestration, UI, ingestion). Each subfolder has a README. The ingest pipeline (`src/ingest/`) drives Mistral OCR → page-chunking → Jina-v4 embedding → Chroma; design locked in `docs/OCR_implementation.md`.

This project uses `uv` (not pip). See `SETUP.md` for install and run. Run tests with `uv run pytest`; lint with `uv run ruff check src/ tests/ scripts/`. Both must be green before merging.

## Where to look first

| If you need to…                              | Open                                          |
| -------------------------------------------- | --------------------------------------------- |
| Understand the project pitch / problem       | `README.md`                                   |
| Find a locked design decision                | `BUILD_SPEC.md` (numbered sections §1–§10)    |
| Find a historical agentic-layer decision     | `docs/RESPONSE_WORKFLOW.md` (L1–L22, pre-merge) |
| Find the current tool-calling agent spec     | `docs/WORKFLOW_SANDBOX.md`                    |
| Get the repo running from a fresh clone      | `SETUP.md`                                    |
| Work inside a subfolder                      | That folder's `README.md`                     |

Precedence when they disagree: `docs/WORKFLOW_SANDBOX.md` (current) > `docs/RESPONSE_WORKFLOW.md` (historical) > `BUILD_SPEC.md`.

## Working in this repo

- **The tool-calling agent is the shipping pipeline on `main`.** The earlier `workflow-sandbox` branch was merged in `f20595a`; its single tool-calling agent + topical verifier per `docs/WORKFLOW_SANDBOX.md` replaced the two-graph LangGraph pipeline. `docs/RESPONSE_WORKFLOW.md` (L1–L22) is preserved as the historical comparison baseline.
- The agent entry point is `src/graph/agent.py::run_agent`. The single tool is `src/graph/tools.py::retrieve`. Post-hoc safety is `src/graph/parse.py` + `src/graph/verifier.py`. The Chainlit UI in `src/ui/app.py` drives the agent via `astream_events` so tool calls surface as "Searching: …" cards.
- Read `BUILD_SPEC.md`, `docs/RESPONSE_WORKFLOW.md`, AND `docs/WORKFLOW_SANDBOX.md` before proposing any architectural change. Decisions in those docs came out of grilling sessions; revisiting one is a new grilling round, not an off-the-cuff suggestion.
- When a change invalidates something stated in this file or a README, update the doc in the same commit.

## Commits

- Match recent `git log` style: short imperative subject, optional short body, no emoji or scope prefixes.
- One commit per locked decision or per finished node, not per file edit.
- Never append `Co-Authored-By: Claude …`.
