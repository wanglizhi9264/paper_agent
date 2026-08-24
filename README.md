# Paper RAG Assistant

Local-first, single-user paper RAG assistant targeting an NVIDIA RTX 2060 (6 GB) host. See [`docs/spec.md`](docs/spec.md) for the normative specification and [`docs/proposal.md`](docs/proposal.md) for the implementation roadmap.

## Status

Phase 0 — repository, quality gates, configuration, structured logging, health endpoints, and Docker Compose dependencies are in place. Ingestion, retrieval and chat arrive in later phases per `docs/proposal.md`.

## Prerequisites

- Python 3.12 (managed by `uv`)
- Node.js 20+ (frontend)
- Docker Desktop (for PostgreSQL 16 and Redis 7)

## Quick Start

```bash
# 1. Backend deps (core + dev)
uv sync --all-groups

# 2. (Optional, only on the GPU host) heavy ML stack
uv sync --extra ml

# 3. Start PostgreSQL + Redis
docker compose up -d

# 4. Configure environment
cp .env.example .env
# edit .env: set PAPER_RAG_DATABASE_URL, PAPER_RAG_REDIS_URL, model ids, LLM endpoint

# 5. Run API
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 6. Health checks
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

### Frontend

```bash
cd frontend
npm ci
npm run dev      # http://127.0.0.1:5173
```

## Quality Gates

Backend:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

Frontend:

```bash
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Integration tests require live PostgreSQL/Redis and are gated behind `--run-integration`:

```bash
uv run pytest -m integration --run-integration
```

Real model smoke tests use the `model_smoke` marker and are never run in CI.

## Configuration

All configuration uses the `PAPER_RAG_` prefix and is loaded from environment variables (or `.env`). See [`.env.example`](.env.example) and `docs/spec.md` §18. Missing required values fail fast at startup.

## Security Notes

- The API binds to `127.0.0.1` by default. Exposing it to LAN/public requires explicit config and is a security-sensitive change — update `docs/spec.md` and this README first.
- No authentication is provided in the MVP.
- `.env`, model weights, uploads, index snapshots and database dumps must never be committed.
