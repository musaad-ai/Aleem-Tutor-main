# Aleem task runner. Container-first; `just dev` keeps the bare-metal
# uv flow alive for sub-second restarts during heavy local iteration.
#
# Install just: `brew install just` (macOS) or see https://github.com/casey/just.

# Show all available recipes when run with no args.
default:
    @just --list

# Build the runtime image. First build is ~3-5 min (torch + transformers).
# Subsequent builds are <30 s when only application code changed — the
# dependency layer is keyed on uv.lock.
build:
    docker compose --env-file /dev/null build

# Start Aleem in the background and print the URL.
up:
    docker compose --env-file /dev/null up -d
    @echo "Aleem: http://localhost:8000"

# Stop and remove the container. Bind mounts and the hf-cache volume survive.
down:
    docker compose --env-file /dev/null down

# Quick bounce after a config change.
restart:
    docker compose --env-file /dev/null restart aleem

# Tail the container logs.
logs:
    docker compose --env-file /dev/null logs -f aleem

# Drop into a shell inside the running container.
shell:
    docker compose --env-file /dev/null exec aleem bash

# Initialise the per-grade Chroma collections (run once after a fresh clone).
init:
    docker compose --env-file /dev/null run --rm -w /app aleem python -m src.retrieval.init_chroma

# Run the ingest pipeline. Pass-through args, e.g. `just ingest --help`.
ingest *ARGS:
    docker compose --env-file /dev/null run --rm -w /app aleem python -m src.ingest {{ARGS}}

# Test suite inside the container. Pass-through args, e.g. `just test -k retrieval`.
test *ARGS:
    docker compose --env-file /dev/null run --rm -w /app aleem pytest {{ARGS}}

# Lint inside the container.
lint:
    docker compose --env-file /dev/null run --rm -w /app aleem ruff check src/ tests/ scripts/

# Bare-metal dev fast-path — no container, uses your local .venv.
# Matches the documented run convention in src/ui/app.py:18.
dev:
    cd src/ui && PYTHONPATH=../.. uv run chainlit run app.py
