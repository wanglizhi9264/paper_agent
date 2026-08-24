# Paper RAG Assistant — Project Memory

> 这是跨任务的简洁事实记录，不是需求来源。规范见 `docs/spec.md`，实施路线见 `docs/proposal.md`。每次实现任务结束后维护本文件，禁止记录密钥或用户论文正文。

## 1. 当前状态

更新时间：2026-08-24

- Phase 0 已完成：仓库、Python/uv 工程、前端工程、Docker Compose、配置加载、结构化日志、request id、health/live 与 health/ready、Ruff/mypy/pytest、前端 lint/typecheck/test/build、CI 工作流。
- Phase 1 已完成：全部 ORM 模型、共享 enums、时间戳 mixin、Alembic async 配置与初始迁移 `0001_initial`。
- Phase 2 已完成（fake parser/indexer 驱动状态机）：Pydantic schemas、document/collection/job services、documents/collections/jobs API routes、ARQ worker `ingestion_task`、ingestion pipeline、ArqEnqueuer + FakeEnqueuer、post-commit enqueue 中间件。
- Phase 3 已完成：三种 Loader（PdfLoader/DocxLoader/MarkdownLoader）与统一 ParsedDocument/Paragraph 模型、Loader Registry、OCR_REQUIRED 检测、golden fixture 测试。
- Phase 4 已完成：确定性 Chunking pipeline（sentence splitter、heading tree、parent merge、fine split、title/table/code chunk、retrieval_content 拼接、SHA-256 hash、ChunkConfig/ChunkResult 模型）。RealChunker 已接入 ingestion pipeline。
- Phase 5 已完成：Embedding protocol（EmbeddingProvider/ModelManifest/EmbeddingResult）、FakeEmbeddingAdapter（deterministic hash→vector）、E5Adapter（sentence-transformers，smoke-only）、FAISS wrapper（IndexIDMap2/IndexFlatIP、save/load/search、L2 normalize、zero-vector reject）、SnapshotManifest（build/validate/save/load、SHA-256 hash、atomic activation）、ingestion pipeline embedding+indexing 阶段（faiss_id 从 max+1 分配、shadow IndexSnapshot 创建+激活+旧快照 superseded）。
- Phase 6 已完成：BM25 analyzer（中英文分词、domain terms 保留、jieba optional）、BM25Index（Okapi BM25 k1=1.5/b=0.75、IDF 公式、scope filter、minimum_should_match、serialization）、RRF fusion（rank from 1、1/(k+rank)、稳定 tie-break）、RetrievalResult 统一模型。
- Phase 7 已完成：Reranker protocol（FakeReranker token overlap、BGEReranker CrossEncoder）、dedup（chunk_id→content_hash 两步）、neighbor_expansion（同文档同 section 前后各 1、expanded_from_chunk_id、score=0）、context builder（SourceBlock、pack_context budget-aware、sentence-boundary truncation、format_source_block、build_citation_map）。
- Phase 8 已完成：LLMProvider protocol（async generate+stream）、FakeLLMProvider（deterministic citation-aware）、OpenAICompatibleProvider（httpx SSE streaming）、prompts（system/rewrite/message builders）、citations（parse [N]、strip invalid、validate_citations）。
- Phase 9 已完成：consistency validation（check_index_health 启动校验 active IndexSnapshot、reconcile_stale_jobs 标记 RUNNING→FAILED+STALE_ON_RESTART、check_document_consistency 检查 READY 文档指向非 READY 版本）。
- Phase 10 已完成：前端 MVP（api/client 扩展 apiPost/apiPostForm/apiDelete/sseStream、types document/collection/chat、features documents/collections/chat 含 SSE state machine、pages DocumentsPage/CollectionsPage/ChatPage 含 source drawer 和 citation badges、AppShell 新增导航、CSS 文档表格/集合卡片/聊天布局/来源面板）。
- Phase 11 已完成：评测框架（retrieval_eval Recall@1/3/5/10/MRR/nDCG/Citation P/R、ablation 7 种配置 dense_only/bm25_only/dense_bm25_rrf/with_rerank/full_pipeline/no_rewrite/no_expansion、format_ablation_markdown、save_results JSON、smoke dataset 3 条）。
- Phase 12 已完成：README 更新（架构说明、模块表、评测说明、全 Phase 状态更新）、memory.md 最终状态更新。
- 后端质量门实测通过：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy app`（73 文件）、`uv run pytest -q`（243 通过，3 集成测试 skipped）。
- 前端质量门实测通过：`npm run lint`、`npm run typecheck`、`npm test -- --run`（6 通过）、`npm run build`（279KB JS / 5KB CSS）。
- 集成测试仍需 live PostgreSQL；Docker 待用户安装。
- 尚未完成：50+ 人工标注评测集（eval/dataset.json 当前为 3 条 smoke）、真实模型 smoke test（需 GPU 主机）、真实端到端集成测试（需 Docker）。
- 已知限制：PyMuPDF 1.28.2 在 macOS arm64 pytest 进程内 segfault；PDF 测试通过 subprocess 运行；生产中 loader 运行在 ARQ worker 不受影响。

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
2. ~~Phase 1：数据模型与初始 Alembic migration。~~ 已完成。
3. ~~Phase 2：上传与异步 ingestion 状态机纵向切片（fake parser/index 跑通端到端状态机）。~~ 已完成。
4. ~~Phase 3：三种 Loader（PDF/DOCX/Markdown）与统一 ParsedDocument，OCR_REQUIRED 检测，golden fixtures。~~ 已完成。
5. ~~Phase 4：确定性 Chunking（sentence splitter、markdown element parser、heading tree、parent merge、title/table/chapter chunk、hash 和 metadata）。~~ 已完成。
6. ~~Phase 5：Dense 索引闭环（Embedding protocol、E5 adapter、模型 manifest、FAISS wrapper、faiss_id mapping、save/load、shadow activation）。~~ 已完成。
7. ~~Phase 6：BM25 与混合检索（中英文 analyzer、BM25 statistics/snapshot、Dense/Sparse retrievers、RRF、统一 RetrievalResult、scope filter）。~~ 已完成。
8. ~~Phase 7：Rerank、去重与上下文工程。~~ 已完成。
9. ~~Phase 8：LLM provider、query rewrite、citations、SSE streaming。~~ 已完成。
10. ~~Phase 9：consistency validation、stale job reconciliation。~~ 已完成。
11. ~~Phase 10：前端 MVP — documents, collections, chat。~~ 已完成。
12. ~~Phase 11：evaluation framework + ablation。~~ 已完成。
13. ~~Phase 12：README 更新、架构说明、memory 最终状态。~~ 已完成。

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
| 2026-08-24 | Evaluation 从 Phase 6 开始左移 | 避免全部功能完成后才发现检索问题 | Phase 6 建 smoke dataset，Phase 11 冻结正式 test |
| 2026-08-24 | 评测证据使用稳定 anchor | Chunk ID 会随 reindex 改变 | quote/page/section/hash 为事实标注，Chunk ID 按 snapshot 派生 |
| 2026-08-24 | Phase 0 完成：uv + React/Vite + compose + health + 质量门 + CI | 按 proposal Phase 0 验收 | 仓库可运行；进入 Phase 1 |
| 2026-08-24 | 重型 ML 依赖放入 optional `[ml]` extra | CI 无 GPU，避免安装 torch | `uv sync --extra ml` 仅在 GPU 主机执行 |
| 2026-08-24 | health/ready 在无 active IndexSnapshot 时返回 ok + detail=not_initialized | Phase 0 无 ingestion，索引缺失是已知态而非故障 | 检索请求时仍返回 INDEX_UNAVAILABLE |
| 2026-08-24 | ORM JSON 列用 `JSON().with_variant(JSONB(), "postgresql")` 工厂 | 单测在 SQLite 跑通 ORM 映射，生产用原生 JSONB | `app/db/types.py:jsonb()` |
| 2026-08-24 | faiss_id 存为 chunks 上的可空 BIGINT + 全局序列 `faiss_id_seq` | chunk 属不可变 DocumentVersion，faiss_id 跨快照稳定，查询时直接由 chunks 表反查 | 迁移创建 SEQUENCE，CHECK faiss_id>=0 |
| 2026-08-24 | 循环 FK（documents→document_versions、system_state→index_snapshots）用 use_alter 延迟创建 | 避免建表顺序死锁，autogenerate 兼容 | 迁移末尾 ALTER TABLE ADD CONSTRAINT |
| 2026-08-24 | system_state 强制单例（id=1，CHECK id=1，迁移 seed） | 全局唯一 active snapshot 指针 | 迁移 INSERT ... ON CONFLICT DO NOTHING |
| 2026-08-24 | 移除 `deferred=True`（active_document_version_id / active_index_snapshot_id） | async session 禁止隐式 lazy load，deferred 列在 async 路由/测试中访问会触发 MissingGreenlet | 列常规加载；如需省列再用显式 select |
| 2026-08-24 | ARQ enqueue 在响应提交后由 http 中间件派发，失败不回滚 DB | spec §10：API 创建 doc+job 同事务提交再 enqueue；失败由 Phase 9 reconciliation 恢复 | job 保持 queued，不产生僵尸 |
| 2026-08-24 | ingestion pipeline 用 `pg_advisory_xact_lock(document_id)`，SQLite 自动跳过 | 同文档同时最多一个写任务；单测无需 PG | `app/services/ingestion.py:_pg_advisory_lock` |
| 2026-08-24 | Phase 2 parser/chunker 为确定性 fake（page=1, char=file_size, chunk=0） | 真实解析在 Phase 3-4；先验证状态机闭环 | DocumentVersion chunk_count=0，active 指针切换 |
| 2026-08-24 | datetime 序列化用 pydantic `field_serializer` 输出 RFC3339+Z | 替代覆写 model_dump，mypy 友好 | schemas 统一 `to_rfc3339` |
| 2026-08-24 | aiosqlite + greenlet 进 dev 依赖 | async ORM 单测需要 | `uv.lock` 更新 |
| 2026-08-24 | PyMuPDF 用 `import pymupdf` 替代 `import fitz` | fitz 旧 API 已废弃 | pdf.py + generators |
| 2026-08-24 | PDF 测试通过 subprocess 运行 PdfLoader | PyMuPDF 1.28.2 在 macOS arm64 pytest 进程内 segfault（与其他 C 扩展冲突）；生产中 loader 运行在 ARQ worker 不受影响 | `tests/fixtures/pdf_runner.py` |
| 2026-08-24 | PDF 文本提取用 `get_text("blocks")` 而非 `get_text("dict")` | 更简洁 API，坐标排序用 block bbox | `app/loaders/pdf.py` |
| 2026-08-24 | Loader 依赖安装用清华镜像 | 用户在中国，PyPI 大包（pymupdf 22.8MB）下载超时 | `uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple` |
| 2026-08-24 | 句末标点用字符串拼接而非 raw regex 字符类 | `r"[。！？!?]"` 在某些环境下 `. ` 不被匹配；改用 `"。！？.!?。！？"` 字符串拼接到 rf-string | `app/chunking/sentence.py` |
| 2026-08-24 | ruff 全局忽略 RUF001/002/003 | 中文全宽标点（。！？，；）是分词器刻意使用的，非歧义 | `pyproject.toml` |
| 2026-08-24 | chunking pipeline 为纯函数，不依赖 DB/I/O | 确定性 golden test：同输入+配置→同 chunk_index/content_hash/retrieval_content | `app/chunking/pipeline.py` |
| 2026-08-24 | RealChunker 写 Chunk ORM 行接入 ingestion pipeline | 替换 Phase 2 fake chunker，真实 chunk 入库 | `app/services/ingestion.py` |
| 2026-08-24 | FakeEmbeddingAdapter 用 MD5 hash→vector | CI 无需下载模型，确定性、可复现；共享 token 的文本产生更高 cosine | `app/embedding/fake.py` |
| 2026-08-24 | FAISS inner index 类型检查放宽为仅 dimension | SWIG 绑定不暴露 IndexFlatIP 具体子类，`isinstance(idx.index, faiss.IndexFlatIP)` 恒为 False | `app/index/faiss_index.py:load` |
| 2026-08-24 | faiss_id 从 max(existing)+1 分配，不从 0 开始 | reindex 时旧 chunk 仍有 faiss_id；从 0 起会违反 unique 约束；生产 PG 用 sequence | `app/services/ingestion.py` |
| 2026-08-24 | manifest sha256 排除 created_at | 时间戳不稳定，排除后同内容→同 hash | `app/index/snapshot.py` |
| 2026-08-24 | numpy/faiss-cpu 用清华镜像安装 | macOS 无预装 | `uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple numpy faiss-cpu` |

## 8. 验证记录

| 日期 | 范围 | 命令/检查 | 结果 |
| --- | --- | --- | --- |
| 2026-08-24 | 文档 | `wc -l`、代码围栏计数、heading inventory、关键术语/占位符 `rg` 检查 | 4 个文件共 1309 行；围栏均成对；章节完整；无阻塞占位符 |
| 2026-08-24 | Phase 0 后端质量门 | `uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy app`、`uv run pytest -q` | ruff/check 通过；format 39 文件已格式化；mypy 26 文件无问题；pytest 20 通过 |
| 2026-08-24 | Phase 0 前端质量门 | `npx oxlint src`、`npx tsc -b --noEmit`、`npx vitest run`、`npx vite build` | oxlint exit 0；typecheck ok；vitest 6 通过；build 成功 |
| 2026-08-24 | Python 3.12 | `uv python install 3.12` | 安装 cpython-3.12.13 |
| 2026-08-24 | Phase 1 ORM/迁移 | `uv run ruff check .`、`ruff format --check .`、`mypy app`(36)、`pytest -q`、`alembic upgrade head --sql`、`alembic downgrade 0001_initial:base --sql` | ruff/mypy 通过；44 单测通过、3 集成测试 skipped；迁移 SQL 可编译 |
| 2026-08-24 | Phase 2 schemas/services/api/worker | `uv run ruff check .`、`ruff format --check .`、`mypy app`(50)、`pytest -q` | ruff/mypy 通过；69 单测通过、3 集成测试 skipped |
| 2026-08-24 | Phase 3 loaders | `uv run ruff check .`、`ruff format --check .`、`mypy app`(55)、`pytest -q` | ruff/mypy 通过；87 单测通过、3 集成测试 skipped |
| 2026-08-24 | Phase 4 chunking | `uv run ruff check .`、`ruff format --check .`、`mypy app`(59)、`pytest -q` | ruff/mypy 通过；116 单测通过、3 集成测试 skipped |
| 2026-08-24 | Phase 5 dense index | `uv run ruff check .`、`ruff format --check .`、`mypy app`(65)、`pytest -q` | ruff/mypy 通过；160 单测通过、3 集成测试 skipped |

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
