# Paper RAG Assistant — Project Memory

> 这是跨任务的简洁事实记录，不是需求来源。规范见 `docs/spec.md`，实施路线见 `docs/proposal.md`。每次实现任务结束后维护本文件，禁止记录密钥或用户论文正文。

## 1. 当前状态

更新时间：2026-08-24

- Phase 0 已完成：仓库、Python/uv 工程、前端工程、Docker Compose、配置加载、结构化日志、request id、health/live 与 health/ready、Ruff/mypy/pytest、前端 lint/typecheck/test/build、CI 工作流。
- 后端质量门实测通过：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy app`（26 文件）、`uv run pytest -q`（20 通过）。
- 前端质量门实测通过：`oxlint src`（exit 0）、`tsc -b --noEmit`、`vitest run`（6 通过）、`vite build`。
- 已初始化 Git 仓库并完成首次提交。
- 尚未实现：Alembic 初始迁移、ORM 模型、Loader、chunking、索引、检索、LLM、前端业务页面、评测。下一步进入 Phase 1（数据模型与迁移）。
- 依赖说明：核心运行时依赖已锁定在 `uv.lock`；重型 ML 栈（torch、sentence-transformers、faiss-cpu、transformers、numpy）放入 `pyproject.toml` 的 `[project.optional-dependencies].ml`，只在 GPU 主机用 `uv sync --extra ml` 安装，CI 不安装。
- Docker 未在本机安装；用户已选择自行安装 Docker Desktop 后运行 `docker compose up -d`。compose 文件已就绪。

## 2. 已确认范围

- 产品：单机、单用户、本地优先的论文 RAG Assistant；MVP 无登录鉴权。
- 设备：NVIDIA RTX 2060，按 6 GB 显存预算；GPU 重任务最大并发 1。
- Loader：PDF、DOCX、Markdown。
- PDF 边界：MVP 只保证文本型论文；OCR、复杂公式和跨页复杂表格延后。
- 后台任务：Redis + ARQ，用户已同意。
- 前端：React + Vite + TypeScript，浅色 Apple 风格，用户已同意。
- Collection：MVP 需要，一篇文档可属于多个集合。
- 生命周期：MVP 需要删除、重新索引、失败重试和跨存储清理。
- 长度：Chunk 参数使用 Unicode 字符数；LLM context 使用 tokenizer tokens。
- 文档语言：中文为主，代码标识和标准技术术语保留英文。

## 3. 模型决策

### 3.1 默认 RTX 2060 profile

```yaml
embedding:
  model_id: intfloat/multilingual-e5-base
  dimension: 768  # 只记录于 manifest，业务代码不得硬编码
  dtype: float16
  normalize: true
  max_tokens: 512
  query_prefix: "query: "
  passage_prefix: "passage: "
reranker:
  model_id: BAAI/bge-reranker-base
  dtype: float16
  max_tokens: 512
  batch_size: 4
generator:
  protocol: openai_compatible_chat_completions
  suggested_local_model: qwen3:4b-instruct
  suggested_runtime: Ollama
  application_context_budget: 8192
gpu_max_concurrency: 1
```

模型 revision 尚未固定；Phase 0/5 第一次准备模型时必须解析实际 commit SHA，写入配置示例和 index manifest。

### 3.2 可选质量 profile

- Embedding：`BAAI/bge-m3`（1024 维）；
- Reranker：`BAAI/bge-reranker-v2-m3`；
- 是否切换必须由 eval 结果支持；Embedding 切换必须生成新 model signature，为全部文档创建兼容 DocumentVersion，并全量重建 IndexSnapshot。

### 3.3 OpenCode 边界

用户希望可以接入本地 OpenCode 已配置的模型。当前决定：不依赖或调用 OpenCode，不读取其凭据；Paper RAG 与 OpenCode 可以显式配置为共享同一个 Ollama/OpenAI-compatible endpoint 和 model。若以后改变此边界，先更新 spec。

## 4. 固定架构决定

- PostgreSQL 是业务事实来源；Redis 不是永久存储。
- API 与 ARQ worker 分进程。
- FAISS 使用 CPU `IndexIDMap2(IndexFlatIP)`；向量必须 L2 normalize。
- BM25 使用自研 Okapi 实现，默认 `k1=1.5`、`b=0.75`。
- 默认检索：Dense 30 + BM25 30 -> RRF(k=60) 30 -> Rerank 8 -> Dedup -> Expansion -> Context packing。
- 单篇解析/切片版本使用 DocumentVersion；整个语料库的 FAISS/BM25 版本使用 IndexSnapshot。
- active FAISS、BM25、manifest 及其引用的 DocumentVersion 必须相容。
- 索引通过 shadow build + validate + atomic activation 更新。
- LLM 只通过 provider protocol 接入；Chat streaming 使用 SSE。
- Collection 删除不删除论文；Document 删除必须从所有 Collection 和索引清理。

## 5. 默认参数摘要

```yaml
small_document_char_threshold: 2048
max_chunk_chars: 800
sentence_merge_num: 6
sentence_on: true
title_chunk_on: true
table_on: true
need_chapter: false
neighbor_window: 1
dense_top_k: 30
bm25_top_k: 30
rrf_k: 60
rrf_top_k: 30
rerank_top_k: 8
max_upload_bytes: 104857600
```

这些值的规范来源是 `docs/spec.md`。改变 Chunk 边界或 embedding 语义的配置需要 reindex；改变 retrieval top-k 需要评测 before/after。

## 6. 当前实施队列

1. ~~Phase 0：初始化仓库、Python/uv 与 React/Vite/TypeScript 工程、compose、health、质量门、CI。~~ 已完成。
2. Phase 1：数据模型与初始 Alembic migration（Document、Collection、association、IngestionJob、DocumentVersion、IndexSnapshot、SystemState、Chunk、Session、Message、RetrievalLog），repository/service 事务边界。
3. Phase 2：上传与异步 ingestion 状态机纵向切片（fake parser/index 跑通端到端状态机）。

尚未授权或不应提前实现：OCR、多用户、云部署、向量数据库、Agent、知识图谱、额外 Loader。

## 7. 决策日志

| 日期 | 决定 | 原因 | 影响 |
| --- | --- | --- | --- |
| 2026-08-24 | MVP 单机单用户且无鉴权 | 目标为本地个人论文助手 | bind、CORS、schema 不含 owner/tenant |
| 2026-08-24 | RTX 2060 / 6 GB 为硬件基线 | 用户明确设备 | 低显存模型档、GPU 并发 1 |
| 2026-08-24 | 默认 E5 base + BGE reranker base | 中英文能力与显存预算平衡 | FAISS dimension 来自 manifest |
| 2026-08-24 | Generator 使用 OpenAI-compatible | 可复用 Ollama、本地或远端已配置模型 | 独立 LLMProvider |
| 2026-08-24 | 不直接集成 OpenCode | OpenCode 是客户端/配置层而非稳定项目内推理依赖 | 只共享显式 endpoint |
| 2026-08-24 | Redis + ARQ 异步 ingestion | 用户确认且适合单机长任务 | API/worker 分离、Job 表 |
| 2026-08-24 | MVP 包含 Collection | 用户确认需要 | many-to-many 与 scope APIs |
| 2026-08-24 | OCR/复杂论文版面延后 | 用户确认 MVP 不需要 | PyMuPDF 文本解析、OCR_REQUIRED |
| 2026-08-24 | Chunk 字符数与 context tokens 分离 | 用户确认 | 两套显式计数和配置名 |
| 2026-08-24 | Phase 0 完成：uv + React/Vite + compose + health + 质量门 + CI | 按 proposal Phase 0 验收 | 仓库可运行；进入 Phase 1 |
| 2026-08-24 | 重型 ML 依赖放入 optional `[ml]` extra | CI 无 GPU，避免安装 torch | `uv sync --extra ml` 仅在 GPU 主机执行 |
| 2026-08-24 | health/ready 在无 active IndexSnapshot 时返回 ok + detail=not_initialized | Phase 0 无 ingestion，索引缺失是已知态而非故障 | 检索请求时仍返回 INDEX_UNAVAILABLE |

## 8. 验证记录

| 日期 | 范围 | 命令/检查 | 结果 |
| --- | --- | --- | --- |
| 2026-08-24 | 文档 | `wc -l`、代码围栏计数、heading inventory、关键术语/占位符 `rg` 检查 | 4 个文件共 1309 行；围栏均成对；章节完整；无阻塞占位符 |
| 2026-08-24 | Phase 0 后端质量门 | `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy app`、`uv run pytest -q` | ruff/check 通过；format 39 文件已格式化；mypy 26 文件无问题；pytest 20 通过 |
| 2026-08-24 | Phase 0 前端质量门 | `npx oxlint src`、`npx tsc -b --noEmit`、`npx vitest run`、`npx vite build` | oxlint exit 0；typecheck ok；vitest 6 通过；build 成功 |
| 2026-08-24 | Python 3.12 | `uv python install 3.12` | 安装 cpython-3.12.13 |

## 9. 未决事项

当前没有阻塞代码生成的产品问题。实现阶段仍需通过实际环境确定：

- RTX 2060 的具体显存容量、CUDA driver/PyTorch 兼容组合；
- 默认模型的固定 Hugging Face revision SHA；
- embedding/rerank 的稳定 batch size 与真实延迟；
- 用户选择的 Generator endpoint/model 及 context limit；
- 50+ eval 问题所用论文集合和人工标注来源。

这些是 Phase 内可验证配置，不改变已批准系统结构。若实测迫使改变架构或 MVP 范围，先与用户确认并更新 spec/proposal。

## 10. 维护模板

完成任务后按需追加：

```text
日期：YYYY-MM-DD
完成：真实存在的能力与文件
决定：做了什么选择、为什么、影响范围
验证：实际运行的命令和结果
剩余：下一步和已知风险
```

保持本文件简洁。详细 API、字段、算法和测试要求只写入 `docs/spec.md`，不要在 memory 中维护第二份容易漂移的规范。
