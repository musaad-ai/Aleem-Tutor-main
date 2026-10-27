# Aleem — عليم

**A curriculum-grounded RAG tutor for Saudi school students.**

Aleem ("knowledgeable" in Arabic) answers student questions using *only* their official Saudi Ministry of Education textbooks. Every answer cites a specific lesson and page — and when the textbook doesn't cover the question, Aleem says so instead of inventing an answer.

---

## The problem

Saudi students who use general-purpose LLMs for homework get answers that are *plausible* but *off-curriculum*: wrong terminology, foreign examples, wrong grade level. They're left second-guessing every answer — and risk memorizing content that gets marked wrong on national exams.

**Aleem's contract:** if it isn't in your textbook, Aleem tells you — and points you to the lessons in your grade that *are* relevant.

---

## What makes Aleem different

| | |
| --- | --- |
| **Grade-isolated** | Each grade has its own index — a Grade 4 student *cannot* receive Grade 10 content. |
| **Grounded by construction** | Corpus is *only* official MoE textbooks from [ien.edu.sa](https://ien.edu.sa). Nothing else. |
| **Lesson-level citations** | Inline `[n]` markers link to the exact lesson, page, and passage. |
| **Refusal as a feature** | No coverage → Aleem declines and surfaces related lessons from the grade. |
| **Bilingual** | Ask in Arabic *or* English — Aleem replies in your language while citing the Arabic textbook. |
| **Model-agnostic** | Pluggable generator (`fake` / `openrouter` / `ollama`) — swap models without touching the pipeline. |

---

## Screenshots

One session — Grade 8, *Digital Skills*.

**1. Pick a subject and grade.** Retrieval is scoped to that grade's index from the first message.

![Aleem welcome screen showing the selected subject (Digital Skills) and grade (8)](docs/assets/grade-subject.png)

**2. Ask in Arabic — get a cited answer.** Numbered markers link to the page; the side card shows the source passage and its `ien.edu.sa` origin.

![Aleem answering an Arabic question with inline numbered citations and a source citation card](docs/assets/hero-chat.png)

**3. Ask in English — Aleem still searches the Arabic textbook.** The "Searching:" card queries the source material in Arabic regardless of the question's language.

![The Searching card showing an English question searched against the Arabic textbook](docs/assets/bilingual-search.png)

**4. Off-curriculum? Aleem refuses — in your language.** RAG pipelines aren't in the Grade 8 book, so Aleem declines **in English** and redirects to lessons that *are* covered.

![Aleem refusing an English question in English and suggesting in-curriculum lessons](docs/assets/refusal.png)

---

## How it works

![Aleem pipeline: question → agent → textbook search loop → verify → cited answer or refusal](docs/assets/howItWorks.png)

*The agent loops — search, read, search again (up to 4×) — then a verifier checks the answer against its sources before it ships. No supporting passage, or an off-topic answer, and Aleem refuses with related lessons instead.*

Under the hood:

- **Agent:** LangGraph `create_react_agent`, one tool, max 4 retrieve calls per turn.
- **Retrieve:** Chroma top-20 → Jina rerank → top-5 chunks.
- **Safety:** a citation parser flags bad/missing `[n]`; a topical verifier (one structured-output LLM call) refuses off-topic answers.
- **Logging:** every turn lands in a JSONL log (tool calls, flags, verdict, scores, latency).

Full design in [`docs/WORKFLOW_SANDBOX.md`](docs/WORKFLOW_SANDBOX.md).

---

## Stack

| Layer | Choice |
| --- | --- |
| **OCR** | [Mistral OCR](https://docs.mistral.ai/studio-api/document-processing/basic_ocr/) — hosted, Arabic-capable |
| **Embeddings** | [jina-embeddings-v4](https://huggingface.co/jinaai/jina-embeddings-v4) — multilingual, long-context |
| **Vector store** | [Chroma](https://www.trychroma.com/) — one collection per grade |
| **Reranker** | [jina-reranker-v2-base-multilingual](https://huggingface.co/jinaai/jina-reranker-v2-base-multilingual) |
| **Generator** | Pluggable via OpenRouter / Ollama (default: `gemini-3.1-pro-preview`) |
| **Orchestration** | [LangGraph](https://langchain-ai.github.io/langgraph/) `create_react_agent` |
| **UI** | [Chainlit](https://chainlit.io/), RTL Arabic layout; chats persist in SQLite |

---

## Scope

- **Loaded today:** Grade 8, 4 textbooks. Adding Grades 4, 7, 10 is an ingestion step, not a redesign (one Chroma collection per grade).
- **Subjects:** Arabic, Islamic studies, social studies, English, Math (Math pending OCR validation).
- **Not yet:** diagram understanding, equation LaTeX, multi-tenant accounts.
- **Data:** official MoE textbooks ([ien.edu.sa](https://ien.edu.sa), [moe.gov.sa](https://moe.gov.sa)), used for non-commercial academic research only; bundled for reproducibility, not for redistribution.

---

## Getting started

Runs end-to-end with **no API key** (`backend: fake`, the default) or a real LLM (`backend: openrouter`).

**Prerequisites:** Docker + [`just`](https://github.com/casey/just) (`brew install just`), git, ~5 GB disk. Optional: `OPENROUTER_API_KEY` for real answers. Skipping Docker? Add `uv`.

**Docker (recommended):**

```bash
git clone <repo-url> Aleem && cd Aleem
cp .env.example .env     # set CHAINLIT_AUTH_SECRET (openssl rand -hex 32)
just build               # build the image (~3–5 min first time)
just init                # create the per-grade Chroma collections
just up                  # → http://localhost:8000
```

**Bare-metal (uv):**

```bash
uv sync && cp .env.example .env
uv run python -m src.retrieval.init_chroma                     # create collections
uv run python scripts/smoke_run.py "what is photosynthesis?"   # agent run, no UI, no key
just dev                                                       # Chainlit UI → :8000
```

For a real LLM, set `backend: openrouter` in `config.yaml` and `just restart`. Full guide — backends, chat history, troubleshooting — in **[`SETUP.md`](SETUP.md)**.

---

## Repository layout

```
Aleem/
├── README.md / SETUP.md   This pitch · install + run guide
├── BUILD_SPEC.md          Locked design decisions (§1–§10)
├── CLAUDE.md / AGENTS.md   Repo map for coding agents
├── config.yaml · justfile  Backend config · task runner
├── docs/                   Design docs + assets/ (screenshots)
├── prompts/                Jinja templates (agent.j2, verifier.j2)
├── scripts/                smoke_run.py (agent, no UI)
├── src/
│   ├── retrieval/          Jina-v4 embedder + Chroma client
│   ├── graph/              Tool-calling agent (agent, tools, verifier, parse)
│   ├── ingest/             OCR → chunking → embedding pipeline
│   └── ui/                 Chainlit app
├── tests/                  pytest suite (fake backend)
├── chroma/ · Data/          Persisted collections · raw PDFs (gitignored)
```

---

## Evaluation

- **Smoke set (60 Q):** 70% from end-of-chapter exercises, 20% should-refuse, 10% adversarial bilingual.
- **Metrics:** retrieval Recall@5, refusal precision/recall, manual faithfulness review.
- **Next:** publish a 150–300-question gold set on HuggingFace; embedding + generator bake-off; RAGAS suite.

---

## Roadmap

- [x] **Grade 8** — end-to-end app with grade-isolated retrieval, citations, and refusal.
- [ ] **More grades** — ingest Grades 4, 7, 10 and more subjects.
- [ ] **Benchmark** — publish the eval set; run bake-offs.
- [ ] **Multimodal** — retrieve diagrams (Jina-v4 image embeddings); LaTeX-aware math OCR.
- [ ] **Pilot** — classroom testing with a Saudi school.

---

## License & use

- **Code:** TBD (likely MIT or Apache 2.0).
- **Textbooks:** © Ministry of Education, Saudi Arabia — non-commercial academic research only, not for redistribution. Deployment beyond this capstone requires MoE permission.

## Acknowledgments

Ministry of Education (textbooks) · [Mistral AI](https://mistral.ai/) (OCR) · [Jina AI](https://jina.ai/) (embeddings + reranker) · the Applied AI Bootcamp. Capstone project, team of 2.
