# Paper RAG Assistant

2026-09-04 检索改进切片：新增可选的 cell-coverage 证据选择、候选池配置、阶段诊断，
以及 chat-only 的两轮有界证据补充工作流；这两项策略默认关闭，并配有 dev-only A/B。
技术来源、实验命令与论文 Agent 路线见
[调研与落地记录](docs/retrieval-upgrade-research.md)。这不是完整 Agent 或发布质量门通过声明。

2026-10-02 起，显式多文档 scope 默认启用保守的 document balance：候选池和最终
Top-K 都为每篇 scoped paper 保留至少一个位置；`all`/`collection` scope 不做配额。
冻结 benchmark 的公开聚合结果见
[2026-10-02 全量重跑报告](docs/gold-benchmark-rerun-2026-10-02.md)。
CJK→英文 chat retrieval rewrite 已完成经授权的 provider A/B；它提高 Recall@10，
但引用指标略降且延迟增加，因此默认仍保持 `preserve`。

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

### Paper Agent 网页与持久化运行（2026-10-16）

Vue 3 问答页现为默认 `/` 与 `/agent`：支持论文范围与技能选择、执行进度与等待计时、Markdown 回答、原文侧栏、中文证据不足提示、取消和恢复。管理/review 页面暂保留 React，旧单轮聊天位于 `/legacy-chat`。迁移依据为用户 2026-10-17 明确要求。

LangGraph 调度有界工具循环；PostgreSQL 保存 checkpoint/事件/租约/fencing/outbox，ARQ
执行并恢复后台任务。默认关闭，不改变现有 chat 的默认检索策略。

在仓库根目录执行，先启动 Docker Desktop 和 `docker compose up -d`，使用已有 ready 论文库。
GPU 检索与 ingestion 使用跨进程 PostgreSQL gate，ARQ max_jobs=1。

```powershell
# 已有 .env 和本机 ML 环境。仅首次迁移；从 .env 取 URL，不打印密钥。
uv run python -c "import os; from app.core.config import get_settings; from alembic.config import Config; from alembic import command; os.environ['PAPER_RAG_DATABASE_URL']=get_settings().database_url; command.upgrade(Config('alembic.ini'),'head')"

# 当前本机 DeepSeek V4：只下载官方 tokenizer/config/license，不下载模型权重。
# 其它模型需使用对应 tokenizer（带 HF chat template），去掉 --chat-format deepseek-v4。
uv run python -m app.cli.paper_agent --setup-tokenizer deepseek-ai/DeepSeek-V4-Flash --chat-format deepseek-v4

# 启动 API + Worker + 前端；仅监听本机，并使用已缓存 E5/BGE。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-agent.ps1
# 浏览器打开 http://127.0.0.1:5173/agent

# 以下 CLI 是辅助入口，不替代网页/API。
uv run python -m app.cli.paper_agent --list-skills
uv run python -m app.cli.paper_agent --query "请说明 DDPM 的核心方法和训练目标" --skill method-analysis

# 控制论文范围 / 继续同一会话（ID 使用程序打印值）
uv run python -m app.cli.paper_agent --document-id DOCUMENT_UUID --query "分析这篇论文的实验" --skill experiment-audit
uv run python -m app.cli.paper_agent --session-id SESSION_UUID --query "它有哪些局限？" --skill paper-reading
uv run python -m app.cli.paper_agent --show-run RUN_UUID
uv run python -m app.cli.paper_agent --resume RUN_UUID
```

首次调用可能需要加载已准备的 E5/BGE。不会自动下载 tokenizer；普通 CI 不下载模型。
每个 run 最多 4 次 action、8 次模型调用、10 次工具调用、4 次 search、32000 累计 token、180 秒。
Ctrl+C 或进程退出后可显式 resume；已提交的工具步骤复用，但期限/预算不重置，
未提交的只读工具/模型调用可重试并可能重复计费。模型/skill/tokenizer 或 active snapshot
变化会拒绝恢复。失败 run 不可 resume，需新 query。JSON action 错误、无效引用或预算错误
返回稳定错误码；日志保存在本机数据库，`--show-run` 不输出论文正文。

完整契约见 [Runtime spec](docs/paper-agent-runtime-spec.md)。公共 API 包括
`POST /api/v1/agent/runs`（必填 Idempotency-Key）、run 查询、events SSE、cancel/resume 与 skills。
网页断开不取消任务，刷新后恢复同一 run；自动恢复最多两次且不重置期限/预算。
原生工具协议适用于经过验证、含工具 chat template 的 tokenizer；当前 DeepSeek V4 使用
显式 JSON adapter。自定义技能可用 `--validate-skill PATH` / `--enable-skill PATH` 审核启用，
不执行 scripts。评测入口为 `python -m eval.agent_eval --help`，需要人工核验的新 dev/holdout；
既有 standard-v2 test 不再用于选参，工程检查通过不表示论文质量发布门通过。

LangGraph 1.2.15（MIT）已锁定；它的 checkpoint 与 LangChain/LangSmith SDK 是传递依赖，
不启用 LangSmith 云追踪，启动脚本明确关闭 tracing。采用服务自有事务 checkpoint，避免
框架 saver 与业务数据库独立双写；未采用额外 PostgreSQL saver。安装体积与实际验证见 memory。

### 本地只读 MCP（2026-10-17）

已提供四个 STDIO 工具：`list_papers`、`search_papers`、`read_chunks`、`read_table`。MCP adapter 通过 loopback API 复用模型，检索在 ARQ worker 执行，不调用内部生成 LLM。实际 STDIO 客户端已验证目录、搜索、原文和表格读取；Codex 配置需要合并后重新加载，配置文件存在不代表当前聊天已接通。

```powershell
# 启用 MCP adapter 依赖；已有模型环境使用 --inexact 保留额外 ML 包
uv sync --inexact --all-groups --extra mcp
./scripts/start-agent.ps1 -McpOnly
# 由 MCP 客户端启动以下入口；它会等待 STDIO 协议输入
uv run --no-sync python -m app.mcp.server --transport stdio
```

将 [Codex 配置示例](examples/mcp/codex-config.toml) 合并到本机配置，按实际目录调整路径。API 侧默认 `PAPER_RAG_MCP_ENABLED=false`，启动脚本会显式启用。完整契约、30 分钟固定上下文、字符续读、范围/版本检查及剩余边界见 [本地 MCP 说明](docs/local-mcp-plan.md)。本地 API/数据库/Redis/worker 仍须运行；云端宿主模型会接收到工具返回的论文摘录。

开发依赖包含 MCP SDK 供真实协议测试；生产非 MCP 安装可用 `--no-dev` 且不选 `mcp` extra。MCP 功能不包含 Python 执行或 LaTeX 文件/编译工具。

此模式不要求 Agent tokenizer，不启动 Vue；需要已准备的论文索引及 E5/BGE 模型。若也要网页 Agent，使用不带 `-McpOnly` 的启动方式。

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

For manual review of unresolved private evidence, start the frontend with `npm --prefix frontend run dev`
and open `http://127.0.0.1:5173/gold-review`. The loopback-only Vite development server
automatically reads the ignored `benchmark_unresolved.json` and `benchmark_repair_report.json`,
matches uploaded PDFs by SHA-256, and reads the Document IR for the report's pinned snapshot.
Missing or inconsistent sources are shown as unavailable; optional manual file selectors remain
under "替换数据或补充材料". These private files are not bundled into the production frontend. Exported review notes are separate
from gold labels and do not permit benchmark freeze. The offline report currently lacks candidate
Chunk text, so candidate IDs or fuzzy scores alone must not be used to confirm a label.

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

Before treating a split as an unseen holdout, audit the resolved labels for structural errors and
cross-split source leakage:

```bash
uv run python -m eval.gold_benchmark_audit \
  eval/private_benchmark/standard_v2/benchmark_resolved.json \
  --expected-total 60 --expected-dev 42 --expected-test 18 \
  --require-leakage-free \
  --output-json eval/private_benchmark/standard_v2/standard_audit_report.json \
  --output-markdown eval/private_benchmark/standard_v2/standard_audit_report.md \
  --output-review-markdown eval/private_benchmark/standard_v2/benchmark_all_questions.md
```

The audit fails if dev/test share an evidence anchor, physical source page, or resolved Chunk, or if
answerability, quote hashes, resolution states, and citation labels are inconsistent. Private PDFs,
questions, evidence text, and generated reports remain ignored and must not be committed.

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
| `frontend/` | Vue 3 问答 + Vite + TypeScript strict；React 管理/review 页面分页面懒加载 |

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
