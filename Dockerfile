# syntax=docker/dockerfile:1.7

# --- builder stage -----------------------------------------------------------
# uv's official Python base image — bundles `uv` + CPython 3.11 (matches
# .python-version exactly). Bookworm-slim keeps the build context small.
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Lockfile-only copy first so this layer caches across code edits.
COPY pyproject.toml uv.lock .python-version ./

# Install runtime + dev deps into /app/.venv. --frozen enforces uv.lock,
# --no-install-project skips the (non-package) project itself. Dev deps
# (pytest, ruff) are kept so `just test` / `just lint` work inside the
# container. For a lean prod image, add `--no-dev` here.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# --- runtime stage -----------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH=/app/.venv/bin:$PATH \
    HF_HOME=/cache/huggingface \
    CHROMA_DIR=/app/chroma

WORKDIR /app

# Pre-installed venv from builder.
COPY --from=builder /app/.venv /app/.venv

# Application code + data. chroma/, Data/, and src/ui/.chainlit are baked
# in so `docker run aleem` works end-to-end without host mounts — matches
# the reproducibility commit (84e8423). docker-compose.yml mounts the
# host copies over these for iterative dev.
COPY src ./src
COPY prompts ./prompts
COPY config.yaml ./
COPY chroma ./chroma
COPY Data ./Data

EXPOSE 8000

# Chainlit expects to be invoked from src/ui/ so it picks up
# .chainlit/config.toml — see src/ui/app.py:18 for the documented run
# convention. PYTHONPATH=/app keeps `from src...` imports resolvable.
WORKDIR /app/src/ui

CMD ["chainlit", "run", "app.py", "--host", "0.0.0.0", "--port", "8000", "--headless"]
