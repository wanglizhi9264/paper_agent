# Architecture

This document describes the runtime architecture of Paper RAG Assistant. For the normative contract, see [`spec.md`](spec.md); for implementation order and trade-offs, see [`proposal.md`](proposal.md).

## 1. System Overview

The system is a local-first, single-user RAG pipeline for academic papers. It runs three processes on a single host:

| Process | Command | Role |
| --- | --- | --- |
| API server | `uv run uvicorn app.main:app` | HTTP endpoints, request handling, SSE streaming |
| ARQ worker | `uv run arq app.workers.settings.WorkerSettings` | Ingestion jobs (parse, chunk, embed, index, delete) |
| Frontend dev server | `npm run dev` (Vite) | React SPA |

Infrastructure (Docker Compose):

| Service | Image | Bind | Purpose |
| --- | --- | --- | --- |
| PostgreSQL 16 | `postgres:16-alpine` | `127.0.0.1:5432` | Primary data store |
| Redis 7 | `redis:7-alpine` | `127.0.0.1:6379` | ARQ job queue + transient state |

All services bind to loopback only. No authentication is provided in the MVP.

## 2. Dependency Direction

```
api ──→ services ──→ domain protocols
worker ──→ services ──→ domain protocols
adapters (loaders/models/index/llm) ──→ protocols
models/db ──→ no api or worker imports
```

- **Routes** (`app/api/`) only parse requests, check permissions/status, call services, and serialize responses. No retrieval algorithm or DB transaction details.
- **Services** (`app/services/`) manage use-cases and transactions. Algorithm modules are independently unit-testable without a database.
- **Adapters** (`app/loaders/`, `app/embedding/`, `app/index/`, `app/retrieval/`, `app/rerank/`, `app/llm/`) implement protocols and are swappable.
- **ORM models** (`app/models/`) have no API or worker imports.

## 3. Data Model

### 3.1 Core Tables

```
documents ──┬── active_document_version_id ──→ document_versions
            │                                    │
            │                                    ├── chunks (1:N)
            │                                    │
            ├── ingestion_jobs (1:N)             │
            │                                    │
            ├── collection_documents (N:M) ──→ collections
            │
            └── sessions.messages

system_state (singleton)
  └── active_index_snapshot_id ──→ index_snapshots

retrieval_logs (audit trail, FK → sessions)
```

### 3.2 Key Entities

| Entity | UUID | Role |
| --- | --- | --- |
| `Document` | UUIDv4 | User-facing paper record; tracks status, SHA-256, page/chunk counts |
| `DocumentVersion` | UUIDv4 | Immutable per-parse version; V2 rows also hold parser signature, IR schema/path/hash/quality, embedding signature, chunks |
| `Chunk` | UUIDv4 | Retrieval unit: raw_content (citation), retrieval_content (embedding/BM25), section_path, page/line range, faiss_id |
| `IngestionJob` | UUIDv4 | Async job: kind (ingest/reindex/delete_cleanup), status, stage, progress, attempt |
| `IndexSnapshot` | UUIDv4 | Immutable FAISS + BM25 + manifest bundle; status (building/active/superseded/failed) |
| `SystemState` | singleton (id=1) | Points to the active IndexSnapshot |
| `Session` | UUIDv4 | Chat session with scope (all/documents/collection) |
| `Message` | UUIDv4 | User or assistant message; citations stored as JSONB |
| `RetrievalLog` | UUIDv4 | Full audit trail of a retrieval+generation call |

### 3.3 FAISS ID Allocation

FAISS uses non-negative `int64` IDs, not UUIDs. The `Chunk.faiss_id` column is a nullable BigInteger mapped to the FAISS `IndexIDMap2`. IDs are allocated lazily during snapshot building:

```python
max_id = SELECT max(faiss_id) FROM chunks
next_id = max_id + 1
for chunk in new_chunks_without_faiss_id:
    chunk.faiss_id = next_id
    next_id += 1
```

This avoids unique constraint violations on reindex and allows the old snapshot to remain valid until the new one is atomically activated.

## 4. Ingestion Pipeline

### 4.1 State Machine

```
queued ──→ parsing ──→ chunking ──→ embedding ──→ indexing ──→ finalizing ──→ ready
  │           │            │             │             │              │
  └───────────┴────────────┴─────────────┴─────────────┴──────────────┘
                              on failure → FAILED
```

Stages set both `IngestionJob.stage` and `IngestionJob.progress`:

| Stage | Progress | Action |
| --- | --- | --- |
| `queued` | 5 | Job enqueued to ARQ |
| `parsing` | 20 | PDF router → validated Canonical IR v2 + staged artifacts; DOCX/MD → `ParsedDocument` |
| `chunking` | 45 | IR-aware table/text chunking or legacy non-PDF chunking → `Chunk` rows |
| `embedding` | 70 | Embed all chunks; stamp `DocumentVersion.embedding_signature` |
| `indexing` | 90 | Build FAISS + BM25 snapshot via `IndexManager` |
| `finalizing` | 99 | Switch `document.active_document_version_id`; mark version READY |
| `ready` | 100 | Document status → READY; job → SUCCEEDED |

### 4.2 Advisory Locking

Each ingestion job acquires a PostgreSQL transaction-level advisory lock keyed on the document UUID (folded to int64). This prevents concurrent jobs on the same document. On non-PG dialects (SQLite in unit tests), the lock is skipped.

### 4.3 Failure Recovery

- On `PipelineError`: job → FAILED with error code; document reverts to READY if it had a prior version, or FAILED otherwise.
- On unexpected exception: job → FAILED with `INTERNAL_ERROR`.
- Reindex failure preserves the prior `DocumentVersion` and active `IndexSnapshot`; the document remains searchable.
- PDF IR is staged below `storage/ir/building/<version>` and atomically moved to
  `storage/ir/versions/<version>` before the database pointers switch. Stale builds and orphan artifacts
  are failed/quarantined at worker startup.
- Delete failure leaves the document in `DELETING` status for retry.

### 4.4 Idempotency

Re-running a SUCCEEDED ingest job is a no-op. The worker checks `job.status == SUCCEEDED` before dispatching.

## 5. Index Snapshot Management

### 5.1 Corpus-Wide Snapshots

The system builds **corpus-wide** snapshots, not per-document indices. Each new or reindexed document triggers `IndexManager.build_corpus_snapshot()`, which:

1. Collects all `READY` documents whose `DocumentVersion.embedding_signature` matches the current embedding model.
2. Includes the pending (new/reindexed) version.
3. Embeds all chunks with the current model.
4. Allocates FAISS IDs for any chunk missing one.
5. Builds a new `FaissIndex` (`IndexIDMap2(IndexFlatIP)`) and `BM25Index`.
6. Writes FAISS, BM25, and manifest to a `building/` directory.
7. Validates the manifest (SHA-256, dimension, document-version map).
8. Atomically renames the complete directory to `indexes/versions/<snapshot_id>`.
9. In one database transaction marks the new snapshot ACTIVE, supersedes the old one, updates
   `SystemState.active_index_snapshot_id`, marks the new DocumentVersion READY, and switches the document pointer.

### 5.2 Manifest

The manifest is a JSON document containing:

- Embedding model id, revision, dimension, signature, pooling, normalize, prefix.
- BM25 analyzer config (name, k1, b).
- Document-version map (`{document_id: version_id}`).
- Chunk count, max FAISS ID.
- SHA-256 of the above (excluding `created_at` for stable hashing).

### 5.3 Atomic Activation

```
building/<snapshot_id>/
  ├── index.faiss
  ├── bm25.json
  └── manifest.json
```

Files are written to a temporary `building/` directory. Missing files fail preflight without moving the
shadow. The whole validated directory is renamed into a unique immutable `versions/` path, then the
database activation transaction switches all pointers. A failed transaction restores the previous
version/snapshot status and marks the new records failed; file paths are never overwritten.

## 6. Search Pipeline

See [`retrieval-design.md`](retrieval-design.md) for the full retrieval design.

```
Query
  │
  ├── Dense: embed query → FAISS search (top-30) → filter by scope
  │
  ├── Sparse: BM25 search (top-30) → filter by scope
  │
  └── RRF fusion (rank=1 start) → rerank (cross-encoder) → top-k
```

Scope filtering happens **before** fusion (invariant #10), not after.

## 7. Chat Pipeline

```
ChatRequest (session_id, query)
  │
  ├── Resolve session scope
  ├── Search corpus (Dense + BM25 + RRF + rerank)
  ├── Build context (source blocks with [N] markers)
  ├── LLM generate (stream or non-stream)
  ├── Validate citations ([N] → chunk_id)
  ├── Persist user + assistant messages
  └── Return answer + sources + citations
```

SSE events: `meta` → `sources` → `delta`* → `done` (or `error`).

## 8. Model Adapter Pattern

All heavy components follow the Protocol + adapter pattern:

| Component | Protocol | Real Adapter | Fake Adapter |
| --- | --- | --- | --- |
| Embedding | `EmbeddingProvider` | `E5Adapter` (sentence-transformers) | `FakeEmbeddingAdapter` (64-dim deterministic) |
| Reranker | `Reranker` | `BGEReranker` (CrossEncoder) | `FakeReranker` (token overlap) |
| LLM | `LLMProvider` | `OpenAICompatibleProvider` (httpx) | `FakeLLMProvider` (canned template) |
| Loader | `Loader` | `PDFLoader`, `DocxLoader`, `MarkdownLoader` | — |

Fakes are activated when `PAPER_RAG_ENV=test` or the model id is set to `fake`. This allows CI to run without GPU, network, or model downloads.

## 9. GPU Memory Strategy

Target: NVIDIA RTX 2060 (6 GB VRAM).

| Component | Dtype | Batch | Strategy |
| --- | --- | --- | --- |
| Embedding (E5-base) | FP16 | 16 | GPU semaphore = 1 |
| Reranker (BGE-base) | FP16 | 4 | Same semaphore |
| LLM (Q4) | Q4 | — | Via Ollama, separate process |

On OOM: reduce batch size once and retry. If still fails, return a stable error. No infinite retry.

The ARQ worker runs `max_jobs=1` (serial), ensuring only one GPU-intensive task runs at a time.

## 10. Frontend Architecture

React + Vite + TypeScript (strict mode):

```
frontend/
  src/
    components/    Reusable UI (SourceDrawer, ChatMessage, etc.)
    hooks/        TanStack Query hooks for server state
    pages/        Documents, Collections, Chat
    api/          Generated types from OpenAPI, fetch wrappers
    stores/       SSE stream state (single parser, not duplicated)
```

- Server state: TanStack Query (no manual cache management).
- SSE streaming: single parser in a custom hook; components subscribe to it.
- Every async page implements loading, empty, error, and success states.
- Destructive operations require confirmation and disable during in-flight.
- UI style: light Apple-style (white/light-gray, near-black text, system blue accents).
