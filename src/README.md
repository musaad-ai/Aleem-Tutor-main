# `src/` — Aleem source code

All Python for the Aleem RAG tutor lives here. Organised by pipeline stage
so each module has a single, narrow responsibility.

## Module map

| Folder         | Purpose                                                              | Status |
| -------------- | -------------------------------------------------------------------- | ------ |
| `retrieval/`   | Chroma client, Jina-v4 embedding function, Jina reranker, collection setup, model prewarm | ✅ |
| `ui/`          | Chainlit app — grade picker, subject picker, persistent history, tool-call cards | ✅ |
| `ingest/`      | OCR (Mistral) + chunking pipeline → adds documents to Chroma         | ✅ |
| `graph/`       | Tool-calling agent + verifier + citation parse (`docs/WORKFLOW_SANDBOX.md` §3). Replaces the old rewrite→decompose→intent→retrieve→self_check→generate pipeline. | ✅ |

> All query-path code lives in `graph/` per `RESPONSE_WORKFLOW.md` L10.

Pipeline flow (left-to-right is offline → online):

```
src/ingest/  →  Chroma collections  →  src/retrieval/  →  src/graph/  →  src/ui/
   (once)         (persisted)            (per query)       (per query)     (live)
```

## Running pieces today

```bash
# From the repo root.

# 1. Install dependencies (uv reads pyproject.toml + uv.lock; manages Python
#    3.11 automatically). For the pip fallback, see SETUP.md §3.
uv sync

# 2. Create the empty Chroma collections.
uv run python -m src.retrieval.init_chroma

# 3. Launch the Chainlit UI — drives the tool-calling agent end-to-end.
#    Chainlit reads its config/markdown/public assets from the CWD, so
#    we run it from inside src/ui/. PYTHONPATH lets `from src...` imports
#    resolve from the repo root.
(cd src/ui && PYTHONPATH=../.. uv run chainlit run app.py)
```

## Reference

- `BUILD_SPEC.md` (repo root) — locked design decisions per pipeline stage.
- `README.md` (repo root) — project overview, problem statement, stack.

## Folder READMEs

Every subfolder has its own `README.md` with module-specific detail. Open
that first if you're working inside a folder.
