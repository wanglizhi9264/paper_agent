# Paper RAG Assistant

Local-first, single-user paper RAG assistant targeting an NVIDIA RTX 2060 (6 GB) host. See [`docs/spec.md`](docs/spec.md) for the normative specification and [`docs/proposal.md`](docs/proposal.md) for the implementation roadmap.

## Status

Production wiring complete. The full pipeline runs end-to-end: upload → parse → chunk → embed → index (FAISS + BM25) → search (Dense + BM25 + RRF + rerank) → context pack → LLM → citation. See [`memory.md`](memory.md) for detailed progress and [`docs/dod-checklist.md`](docs/dod-checklist.md) for the Definition of Done checklist.

PDF Ingestion V2 is a staged architecture upgrade. V2-0 through V2-2 are accepted; V2-3 through V2-8 have code contracts, adapters, deterministic tests, migration, production activation wiring, and fail-closed release gates. On 2026-08-26 PostgreSQL migration round-trip and production Worker startup/recovery were verified locally. Real Docling/MinerU smoke, private-paper A/B, V2 reindex quality, and the private benchmark release gate remain explicitly pending. See [`docs/pdf-ingestion-v2-spec.md`](docs/pdf-ingestion-v2-spec.md) and [`docs/pdf-ingestion-v2-handoff.md`](docs/pdf-ingestion-v2-handoff.md).

## Prerequisites

- Python 3.12 (managed by `uv`)
- Node.js 20+ (frontend)
- Docker Desktop (for PostgreSQL 16 and Redis 7)
- NVIDIA RTX 2060 6 GB (for production embedding/rerank; CI and local tests use deterministic fakes)

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

# 6. Start the ARQ worker (processes upload/reindex/delete jobs)
uv run arq app.workers.settings.WorkerSettings

# 7. Run the API server
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

# 8. Health checks
curl http://127.0.0.1:8000/health/live
curl http://127.0.0.1:8000/health/ready
```

### Frontend

```bash
cd frontend
npm ci
npm run dev      # http://127.0.0.1:5173
```

### Upload a paper and ask a question

```bash
# Upload a PDF
curl -F "file=@paper.pdf" http://127.0.0.1:8000/api/v1/documents

# Check job status (replace {document_id} and {job_id})
curl http://127.0.0.1:8000/api/v1/documents/{document_id}/jobs/{job_id}

# Create a chat session
curl -X POST http://127.0.0.1:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "My Session", "scope": {"type": "all"}}'

# Ask a question (replace {session_id})
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"sessionId": "{session_id}", "query": "What is the main contribution?"}'

# Stream a response via SSE
curl -N -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"sessionId": "{session_id}", "query": "Summarize the method"}'
```

## API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/health/live` | Liveness probe |
| GET | `/health/ready` | Readiness (PostgreSQL, Redis, index snapshot) |
| POST | `/api/v1/documents` | Upload a document (PDF/DOCX/MD, max 100 MiB) |
| GET | `/api/v1/documents` | List documents |
| GET | `/api/v1/documents/{id}` | Get document detail |
| DELETE | `/api/v1/documents/{id}` | Delete a document |
| POST | `/api/v1/documents/{id}/reindex` | Reindex a document |
| GET | `/api/v1/documents/{id}/jobs` | List jobs for a document |
| GET | `/api/v1/jobs/{id}` | Get job status |
| POST | `/api/v1/collections` | Create a collection |
| GET | `/api/v1/collections` | List collections |
| POST | `/api/v1/collections/{id}/documents` | Add document to collection |
| DELETE | `/api/v1/collections/{id}/documents/{doc_id}` | Remove document from collection |
| POST | `/api/v1/search` | Hybrid search (Dense + BM25 + RRF + rerank) |
| POST | `/api/v1/sessions` | Create a chat session |
| GET | `/api/v1/sessions` | List sessions |
| GET | `/api/v1/sessions/{id}` | Get session detail |
| GET | `/api/v1/sessions/{id}/messages` | List messages in a session |
| DELETE | `/api/v1/sessions/{id}` | Delete a session |
| POST | `/api/v1/chat` | Non-streaming chat with citations |
| POST | `/api/v1/chat/stream` | Streaming chat via SSE |
| GET | `/api/docs` | OpenAPI / Swagger UI |

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

### PDF Ingestion V2 private release gate

After the six private papers are reindexed and the 52 answerable labels are resolved, run the
fail-closed release evaluator against the live API. All input/output paths below are ignored by Git:

```bash
uv run python -m eval.pdf_v2_release \
  --dataset eval/private_benchmark/dataset.resolved.json \
  --hard-case-evidence eval/private_benchmark/hard-case-evidence.json \
  --corpus-evidence eval/private_benchmark/corpus-evidence.json \
  --output eval/results/pdf-v2-release \
  --allow-live-api
```

The command refuses to run unless the dataset has exactly 60 unique dev/test questions, exactly
52 answerable questions have resolved chunk labels, all 11 hard cases pass page/binding/bbox checks,
six corpus documents and all runtime/recovery gates are green. A complete run always saves
`predictions.json`, `metrics.json`, `manifest.json`, and `summary.md`; unmet metric thresholds produce
exit code 1 and a FAILED report. Invalid or incomplete prerequisites produce exit code 2 before any
predictions are generated.

`corpus-evidence.json` is an operator-produced acceptance record, not an application artifact. It
must contain six document records (`sha256_match`, `status`, positive page/chunk counts), the active
snapshot ID and reload/stability checks, six 64-character parser signatures, pinned embedding/
reranker/generator revisions, `v2_table_citation_bbox_rate: 1.0`, and explicit booleans for backend,
frontend, integration, model-smoke, migration, atomic-activation, rollback, and recovery gates. The
release runner validates every field and embeds the parser/model/snapshot manifests in its report.

Real model smoke tests use the `model_smoke` marker and are never run in CI.

## Architecture

```
                     ┌──────────────────────────────────────────────────┐
                     │                  FastAPI (app.main)                │
                     │  health · documents · collections · jobs ·        │
                     │  search · sessions · chat (SSE)                   │
                     └───────────────────┬──────────────────────────────┘
                                         │
                              ┌──────────▼──────────┐
                              │     Services       │
                              │  ingestion · retrieval │
                              │  document · collection · job │
                              │  consistency · arq_enqueuer │
                              └──────┬────────┬─────┘
                                     │        │
              ┌──────────────────────▼─┐  ┌──▼──────────────────┐
              │   Domain Protocols     │  │      Adapters       │
              │  EmbeddingProvider    │  │  loaders (pdf/docx/md) │
              │  Reranker             │  │  embedding/e5        │
              │  LLMProvider           │  │  index/faiss         │
              │  FakeParser/Chunker   │  │  retrieval/bm25      │
              │  FileRemover           │  │  rerank/bge          │
              └───────────────────────┘  │  llm/openai-compat  │
                                         │  chunking/pipeline   │
              ┌──────────────────────┐   └──────────────────────┘
              │  ARQ Worker           │
              │  ingestion:{kind}     │
              │  max_jobs=1 · 5 retries│
              └───────────────────────┘
```

**Key modules:**

| Module | Description |
| --- | --- |
| `app/loaders/` | PDF, DOCX, Markdown → unified `ParsedDocument` |
| `app/chunking/` | Deterministic sentence splitter, heading tree, chunking pipeline |
| `app/embedding/` | `EmbeddingProvider` protocol, E5 adapter, deterministic fake |
| `app/index/` | FAISS `IndexIDMap2(IndexFlatIP)`, snapshot manifest, `IndexManager` (corpus-wide atomic snapshots) |
| `app/retrieval/` | BM25 index, RRF fusion, `RetrievalResult` |
| `app/rerank/` | Cross-encoder protocol, BGE adapter, deterministic fake |
| `app/context/` | Dedup, neighbor expansion, token-budget context packing, citation map |
| `app/llm/` | `LLMProvider` protocol, OpenAI-compatible adapter, prompt templates, citation parser |
| `app/services/` | Ingestion pipeline (state machine), retrieval orchestration, consistency validation, stale job reconciliation |
| `app/api/` | FastAPI routes: health, documents, collections, jobs, search, sessions, chat |
| `app/models/` | SQLAlchemy ORM: Document, DocumentVersion, Chunk, Collection, IngestionJob, IndexSnapshot, SystemState, Session, Message, RetrievalLog |
| `app/db/` | Async SQLAlchemy engine + session management |
| `frontend/` | React + Vite + TypeScript: Documents, Collections, Chat (SSE streaming, source drawer) |

See [`docs/architecture.md`](docs/architecture.md) for detailed architecture and [`docs/retrieval-design.md`](docs/retrieval-design.md) for the retrieval pipeline design.

## Evaluation

```bash
# Run ablation with smoke dataset
uv run python -m eval.ablation eval/dataset.json
```

Metrics: Recall@1/3/5/10, MRR, nDCG@K, Citation Precision/Recall. Replace `eval/dataset.json` with 50+ human-annotated questions before final acceptance (spec §21).

The ablation framework runs 7 configurations: dense-only, BM25-only, Dense+BM25+RRF, with-rerank, full-pipeline, no-rewrite, no-expansion. Results are output as JSON and a Markdown summary table.

## Configuration

All configuration uses the `PAPER_RAG_` prefix and is loaded from environment variables (or `.env`). See [`.env.example`](.env.example) and `docs/spec.md` §18. Missing required values fail fast at startup.

| Variable | Default | Description |
| --- | --- | --- |
| `PAPER_RAG_DATABASE_URL` | (required) | PostgreSQL asyncpg connection string |
| `PAPER_RAG_REDIS_URL` | (required) | Redis URL for ARQ |
| `PAPER_RAG_STORAGE_DIR` | `./storage` | Root for uploads, indexes, tmp |
| `PAPER_RAG_EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | HuggingFace model id |
| `PAPER_RAG_EMBEDDING_DEVICE` | `cuda:0` | torch device |
| `PAPER_RAG_EMBEDDING_DTYPE` | `float16` | FP16 for RTX 2060 |
| `PAPER_RAG_RERANK_MODEL` | `BAAI/bge-reranker-base` | Cross-encoder model id |
| `PAPER_RAG_PDF_PARSER` | `auto` | PDF parser: auto/pymupdf/docling/mineru |
| `PAPER_RAG_DOCLING_PYMUPDF_TABLE_FALLBACK` | `true` | Recover table structure/text on pages where Docling found no table |
| `PAPER_RAG_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible endpoint (Ollama default) |
| `PAPER_RAG_LLM_MODEL` | `qwen3:4b-instruct` | Generator model name |
| `PAPER_RAG_GPU_MAX_CONCURRENCY` | `1` | GPU semaphore (RTX 2060 constraint) |
| `PAPER_RAG_HOST` | `127.0.0.1` | Bind address (loopback only) |
| `PAPER_RAG_PORT` | `8000` | API port |

Set `PAPER_RAG_ENV=test` or any model id to `fake` to use deterministic fakes (no GPU, no network, no model download).

## Troubleshooting

See [`docs/troubleshooting.md`](docs/troubleshooting.md) for common issues:
- Worker not picking up jobs
- Index snapshot missing or incompatible
- OOM on GPU
- LLM unavailable / 503
- PDF parsing errors
- Frontend API connection refused

## Security Notes

- The API binds to `127.0.0.1` by default. Exposing it to LAN/public requires explicit config and is a security-sensitive change — update `docs/spec.md` and this README first.
- No authentication is provided in the MVP.
- `.env`, model weights, uploads, index snapshots and database dumps must never be committed.
