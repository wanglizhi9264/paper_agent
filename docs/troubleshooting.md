# Troubleshooting

Common issues and solutions for Paper RAG Assistant.

## Infrastructure

### Worker not picking up jobs

**Symptom:** Documents stay in `queued` status; jobs never transition to `running`.

**Diagnosis:**

```bash
# Check if Redis is running
docker compose ps redis

# Check ARQ queue
docker compose exec redis redis-cli LLEN arq:queue

# Check worker logs
uv run arq app.workers.settings.WorkerSettings  # run in foreground to see logs
```

**Common causes:**

1. **Worker not started** — Start it: `uv run arq app.workers.settings.WorkerSettings`
2. **Redis URL mismatch** — Verify `PAPER_RAG_REDIS_URL` in `.env` matches docker-compose Redis bind (`redis://127.0.0.1:6379/0`)
3. **Worker crashed** — Check for import errors or missing dependencies; run `uv sync --all-groups` and `uv sync --extra ml` if on the GPU host
4. **Job already succeeded** — The worker skips already-SUCCEEDED jobs (idempotency). Create a reindex job instead.

### PostgreSQL connection refused

**Symptom:** `connection refused` or `password authentication failed`.

**Fix:**

```bash
# Start PostgreSQL
docker compose up -d postgres

# Wait for healthcheck
docker compose ps postgres  # wait for "healthy"

# Verify connection
docker compose exec postgres psql -U paper_rag -d paper_rag -c "SELECT 1;"
```

### Redis connection refused

**Symptom:** ARQ worker cannot enqueue/dequeue jobs.

**Fix:**

```bash
# Start Redis
docker compose up -d redis

# Verify
docker compose exec redis redis-cli ping  # should return PONG
```

## Index Issues

### Index snapshot missing or incompatible

**Symptom:** `GET /health/ready` returns `503` with `index_snapshot: down`.

**Diagnosis:**

```sql
-- Check system state
SELECT id, active_index_snapshot_id FROM system_state;

-- Check snapshot status
SELECT id, status, embedding_signature, faiss_path, bm25_path
FROM index_snapshots
WHERE id = (SELECT active_index_snapshot_id FROM system_state WHERE id = 1);
```

**Fix:**

1. **No active snapshot** — Upload a document to trigger snapshot building. If the first upload fails, check the worker logs.
2. **FAISS/BM25 file missing** — Files may have been deleted. Reindex any ready document to rebuild the snapshot:
   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/documents/{id}/reindex
   ```
3. **Embedding signature mismatch** — The active snapshot was built with a different embedding model. This happens if `PAPER_RAG_EMBEDDING_MODEL` was changed. Reindex all documents:
   ```bash
   # For each ready document:
   curl -X POST http://127.0.0.1:8000/api/v1/documents/{id}/reindex
   ```

### "Index is unavailable" error on search

**Symptom:** `POST /api/v1/search` returns `503` with `INDEX_UNAVAILABLE`.

**Fix:** No documents have been uploaded yet, or the first upload is still in progress. Wait for at least one document to reach `ready` status.

### "Configured embedding model does not match the active index"

**Symptom:** Search returns `503` with `INDEX_INCOMPATIBLE`.

**Cause:** The embedding model configuration was changed after documents were indexed.

**Fix:** Reindex all ready documents. Each reindex triggers a corpus-wide snapshot rebuild with the current model.

## GPU and Model Issues

### OOM (Out of Memory) on GPU

**Symptom:** Worker fails at `embedding` stage with CUDA OOM error.

**Fix:**

1. Reduce `PAPER_RAG_EMBEDDING_BATCH_SIZE` in `.env` (e.g., from 16 to 8 or 4).
2. Reduce `PAPER_RAG_RERANK_BATCH_SIZE` (e.g., from 4 to 2).
3. Ensure no other GPU process is running (`nvidia-smi`).
4. The system retries once with a smaller batch automatically. If it still fails, the job is marked FAILED.

### Embedding model not available

**Symptom:** `EMBEDDING_UNAVAILABLE` error.

**Fix:**

1. Install ML dependencies: `uv sync --extra ml`
2. If on a machine without GPU, set `PAPER_RAG_EMBEDDING_DEVICE=cpu` and `PAPER_RAG_EMBEDDING_DTYPE=float32`
3. For testing without GPU/network, set `PAPER_RAG_ENV=test` or `PAPER_RAG_EMBEDDING_MODEL=fake`

### Reranker unavailable (graceful degradation)

**Symptom:** Search works but `degraded_reasons` includes `"RERANK_UNAVAILABLE"`.

**Cause:** The BGE reranker model failed to load (missing dependency, OOM, or model not downloaded).

**Fix:**

1. Install ML dependencies: `uv sync --extra ml`
2. Check `PAPER_RAG_RERANK_MODEL` is valid
3. If GPU memory is tight, reduce `PAPER_RAG_RERANK_BATCH_SIZE`

The system continues to work without reranking, just with lower relevance quality.

## LLM Issues

### LLM unavailable / 503

**Symptom:** Chat returns `503` with `LLM_UNAVAILABLE`.

**Fix:**

1. Verify `PAPER_RAG_LLM_BASE_URL` is correct (default: `http://127.0.0.1:11434/v1` for Ollama)
2. Ensure Ollama (or compatible server) is running: `curl http://127.0.0.1:11434/v1/models`
3. Verify `PAPER_RAG_LLM_MODEL` matches a model available on the server
4. For testing without a server, set `PAPER_RAG_ENV=test` or `PAPER_RAG_LLM_MODEL=fake`
5. For SSE streaming, verify the server supports `stream: true` in chat completions

### SSE stream disconnects or returns error event

**Symptom:** SSE stream ends with `event: error` instead of `event: done`.

**Fix:** The LLM provider failed mid-stream. The system rolls back the database transaction. Retry the chat request. If persistent, check LLM server logs and network.

### Citation markers missing from answer

**Symptom:** LLM response has no `[N]` citations or has invalid markers.

**Cause:** The model may not follow the citation instruction well. Invalid markers are automatically stripped.

**Fix:** Try a different `PAPER_RAG_LLM_MODEL` with better instruction-following capability.

## Document Parsing Issues

### PDF parsing fails

**Symptom:** Job fails at `parsing` stage with `PARSER_ERROR` or `EMPTY_DOCUMENT`.

**Common causes:**

1. **Scanned PDF (no text layer)** — OCR is not supported in the MVP. The PDF must have a text layer (born-digital PDFs).
2. **Corrupted PDF** — Verify the file opens in a PDF reader.
3. **Empty content** — The PDF has no extractable text (e.g., image-only pages).
4. **Docling unavailable after fast-path rejection** — install the pinned layout extra with
   `uv sync --extra pdf-layout`, run `python -m app.cli.docling_setup`, and pin the emitted model SHA values.
5. **IR validation failure** — inspect the job's stable error code and the quarantined artifacts under
   `storage/tmp/failed/<job_id>`; do not edit the canonical JSON and activate it manually.

### PDF V2 migration or recovery fails

Run `uv run alembic current` and confirm the head is `0002_pdf_ingestion_v2`. The migration only adds
nullable V2 columns, so legacy versions remain readable. Worker startup marks stale `building`
DocumentVersion/IndexSnapshot rows failed and quarantines orphan IR directories. If a reindex fails,
verify `documents.active_document_version_id` and `system_state.active_index_snapshot_id` still reference
the prior ready/active records before retrying the failed job.

### PDF V2 release evaluator exits 2 or 1

Exit code 2 is a prerequisite failure: the resolved dataset is not exactly 60 questions/52
answerable labels, an 11-case binding/page/bbox check failed, the six-document snapshot evidence is
incomplete, or `--allow-live-api` was omitted. Fix the named input; do not weaken the validator or
edit frozen labels to match current retrieval output.

Exit code 1 means a complete 60-question baseline was generated but at least one candidate metric
or request-error gate failed. Keep `predictions.json`, `metrics.json`, the parser/index manifest and
error categories under ignored `eval/results/`, then fix the pipeline and create a new run directory.
Never overwrite a failed baseline and report it as passed.

### DOCX or Markdown parsing fails

**Symptom:** Job fails at `parsing` stage.

**Fix:**

1. Verify the file is a valid DOCX (Office Open XML, not legacy `.doc`)
2. For Markdown, ensure the file is UTF-8 encoded
3. Check file extension matches content (the system validates both)

### Upload fails with 413 or 400

**Symptom:** `POST /api/v1/documents` returns 413 or 400.

**Fix:**

1. File must be ≤ 100 MiB (`PAPER_RAG_MAX_UPLOAD_BYTES`)
2. Extension must be `.pdf`, `.docx`, or `.md`
3. Content must match extension (e.g., a `.pdf` file that's actually a ZIP will be rejected)

## Frontend Issues

### API connection refused

**Symptom:** Frontend shows error states or cannot connect.

**Fix:**

1. Verify the API server is running: `curl http://127.0.0.1:8000/health/live`
2. Check `PAPER_RAG_CORS_ALLOW_ORIGINS` includes the Vite dev server URL (`http://127.0.0.1:5173` by default)
3. If using a custom port, update both the API `PAPER_RAG_PORT` and the frontend API base URL

### SSE stream not rendering

**Symptom:** Chat messages don't stream; text appears all at once.

**Fix:**

1. Check browser console for EventSource errors
2. Verify no proxy is buffering SSE (nginx needs `proxy_buffering off`)
3. The API sets `X-Accel-Buffering: no` and `Cache-Control: no-cache` to prevent buffering

## Database Issues

### Migration failure

**Symptom:** `alembic upgrade head` fails.

**Fix:**

```bash
# Check current migration state
uv run alembic current

# If stuck, inspect the database
docker compose exec postgres psql -U paper_rag -d paper_rag -c "SELECT * FROM alembic_version;"

# Reset (destructive — only for development)
docker compose down -v  # removes volumes
docker compose up -d
uv run alembic upgrade head
```

### Advisory lock not released

**Symptom:** A job is stuck in `running` status and new jobs on the same document don't progress.

**Fix:** PostgreSQL advisory locks are transaction-level and automatically released when the transaction ends. If a worker crashed mid-transaction, the lock is released when the connection is closed. Restarting the worker should resolve this. If the job is truly stuck:

```sql
-- Check for stuck jobs
SELECT id, document_id, status, stage, started_at
FROM ingestion_jobs
WHERE status = 'running' AND started_at < NOW() - INTERVAL '30 minutes';

-- Mark as failed for retry
UPDATE ingestion_jobs SET status = 'failed', error_code = 'TIMEOUT'
WHERE id = '<job-uuid>';
```

## Cleanup

### Reset everything (development only)

```bash
# Stop services and remove volumes
docker compose down -v

# Remove uploaded files and indexes
rm -rf storage/uploads/* storage/indexes/*

# Restart and rebuild
docker compose up -d
uv run alembic upgrade head
```
