# Paper RAG Assistant

Local-first, single-user paper RAG assistant targeting an NVIDIA RTX 2060 (6 GB) host. See [`docs/spec.md`](docs/spec.md) for the normative specification and [`docs/proposal.md`](docs/proposal.md) for the implementation roadmap.

## Status

Phases 0–12 complete. The full pipeline is implemented: upload → parse → chunk → embed → index → search (BM25+Dense+RRF) → rerank → context pack → LLM → citation. See [`memory.md`](memory.md) for detailed progress.

## Prerequisites

- Python 3.12 (managed by `uv`)
- Node.js 20+ (frontend)
- Docker Desktop (for PostgreSQL 16 and Redis 7)
- NVIDIA RTX 2060 6 GB (for production embedding/rerank; CI uses deterministic fakes)

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

# 5. Apply database migrations
uv run alembic upgrade head

# 6. Run API
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
PAPER_RAG_DATABASE_URL=postgresql+asyncpg://paper_rag:paper_rag_dev@127.0.0.1:5432/paper_rag \
  uv run pytest -m integration --run-integration
```

Real model smoke tests use the `model_smoke` marker and are never run in CI.

## Architecture

```
API (FastAPI) ──→ Services ──→ Domain Protocols
     ↑                  ↓
Worker (ARQ) ──→ Services ──→ Adapters (Loaders, Embedding, FAISS, BM25, Reranker, LLM)
```

**Key modules:**

| Module | Description |
| --- | --- |
| `app/loaders/` | PDF, DOCX, Markdown → unified `ParsedDocument` |
| `app/chunking/` | Deterministic sentence splitter, heading tree, chunking pipeline |
| `app/embedding/` | `EmbeddingProvider` protocol, E5 adapter, deterministic fake |
| `app/index/` | FAISS `IndexIDMap2(IndexFlatIP)`, snapshot manifest, atomic activation |
| `app/retrieval/` | BM25 index, RRF fusion, `RetrievalResult` |
| `app/rerank/` | Cross-encoder protocol, BGE adapter, deterministic fake |
| `app/context/` | Dedup, neighbor expansion, token-budget context packing, citation map |
| `app/llm/` | `LLMProvider` protocol, OpenAI-compatible adapter, prompt templates, citation parser |
| `app/services/` | Ingestion pipeline, consistency validation, stale job reconciliation |
| `frontend/` | React + Vite + TypeScript: Documents, Collections, Chat (SSE streaming, source drawer) |

## Evaluation

```bash
# Run ablation with smoke dataset
uv run python -m eval.ablation eval/dataset.json
```

Metrics: Recall@1/3/5/10, MRR, nDCG@K, Citation Precision/Recall. Replace `eval/dataset.json` with 50+ human-annotated questions before final acceptance (spec §21).

## Configuration

All configuration uses the `PAPER_RAG_` prefix and is loaded from environment variables (or `.env`). See [`.env.example`](.env.example) and `docs/spec.md` §18. Missing required values fail fast at startup.

## Security Notes

- The API binds to `127.0.0.1` by default. Exposing it to LAN/public requires explicit config and is a security-sensitive change — update `docs/spec.md` and this README first.
- No authentication is provided in the MVP.
- `.env`, model weights, uploads, index snapshots and database dumps must never be committed.
