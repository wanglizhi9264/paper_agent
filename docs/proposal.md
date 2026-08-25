# Paper RAG Assistant 开发提案

> 状态：Approved（恢复实施中）
> 版本：1.2.0
> 最后更新：2026-08-25
> 对应规格：[`docs/spec.md`](./spec.md)

## 1. 提案摘要

本项目拟构建一个本地优先、面向中英文论文阅读的 RAG Assistant。MVP 在 RTX 2060 单机环境上完成从文档上传、结构化解析、混合检索、重排、上下文构建到带引用回答的完整闭环，并提供 Collection、任务状态、调试接口、评测与消融能力。

提案采取“检索质量优先、模型可替换、索引可重建”的路线：PostgreSQL 保存业务事实，FAISS 与 BM25 是可重建快照；Embedding、Reranker 和 LLM 都通过 adapter 隔离。开发不从一次性聊天 Demo 开始，而按可独立验收的纵向切片推进。

## 2. 背景与问题

通用聊天工具处理论文时经常出现以下问题：

- 长论文无法稳定保留章节、页码和表格上下文；
- 纯向量检索容易漏掉术语、缩写、公式变量和数字；
- 纯关键词检索无法处理语义改写和跨语言提问；
- 回答看似合理但引用无法定位到原文；
- 多轮追问的指代会降低检索召回；
- 模型或切片参数变化后缺少可复现评测，无法判断改进是否真实。

因此系统必须把 ingestion、chunking、hybrid retrieval、reranking、context engineering、generation、citation 和 evaluation 视为同一产品链路，而不是只实现 UI 聊天。

## 3. 成功标准

### 3.1 产品成功

- 用户无需命令行即可上传论文、创建集合、查看处理进度、提问和展开来源；
- 每个引用能回溯到唯一 Chunk，并展示文档、章节、页码或行号；
- 删除、重新索引和失败重试可从 UI/API 完成；
- 单机重启后文档、会话和索引可恢复。

### 3.2 技术成功

- 50+ 人工标注问题上生成完整 Recall/MRR/nDCG 和消融报告；
- 任何 embedding 配置变化都会产生新 model signature、DocumentVersion 和 IndexSnapshot，不会发生维度错配；
- active FAISS、BM25、manifest 及其引用的 DocumentVersion 始终相容；
- 检索各阶段可观测、可单测、可用 deterministic fake 复现；
- RTX 2060 上不会因无界 GPU 并发导致服务随机崩溃。

具体门槛以 `docs/spec.md` 的 Definition of Done 为准。

## 4. 方案概览

### 4.1 核心数据流

```text
Upload
  -> durable file + Document + Job
  -> ARQ ingestion worker
  -> ParsedDocument
  -> deterministic chunks
  -> embedding / BM25 statistics
  -> shadow index snapshot
  -> atomic activation

Question
  -> scope validation
  -> standalone rewrite
  -> Dense + BM25
  -> RRF
  -> cross-encoder rerank
  -> dedup + expansion
  -> token-budget packing
  -> OpenAI-compatible LLM
  -> citation validation
  -> SSE + persistent message/log
```

### 4.2 为什么选择混合检索

论文问答同时包含自然语言语义与精确术语。Dense 负责同义表达和跨语言语义，BM25 负责模型名、缩写、指标和数字；RRF 使用排名而不是混合不同量纲的原始分数，作为稳定默认。Cross-encoder 只处理融合后的少量候选，在 RTX 2060 的资源预算内提高精排质量。

### 4.3 为什么选择本地 FAISS + 自研 BM25

MVP 是单机单用户，数据量可控。FAISS 精确内积索引和本地 BM25 能减少外部基础设施，便于理解、调试与离线运行。代价是写并发和快照一致性需要自行管理，因此提案明确采用单写者、影子构建、manifest 校验和原子切换。达到百万级 Chunk、多节点或高写并发前不引入分布式向量数据库。

### 4.4 为什么 API 与 worker 分离

PDF 解析和 embedding 是长任务。将其放在请求进程会造成超时、重复处理和不可观察失败。Redis + ARQ 足以覆盖单机异步任务，同时比完整 Celery 部署更轻。PostgreSQL job 是事实来源，Redis 队列只负责调度。

## 5. 模型策略

### 5.1 RTX 2060 默认档

- Embedding：`intfloat/multilingual-e5-base`；中英文覆盖、768 维、输入窗口与 800 字符 Chunk 匹配；
- Reranker：`BAAI/bge-reranker-base`；中英文 cross-encoder；
- Generator：默认通过 Ollama OpenAI-compatible endpoint 使用 `qwen3:4b-instruct` 的 Q4 量化版本，也可换成已配置的远端模型；
- GPU 重任务并发：1；embedding/rerank batch 根据真实显存压测确定，起始值分别为 16/4。

默认档的目标是可运行与可评测，而不是预先断言它一定优于 BGE-M3。模型卡显示 E5 的最大位置长度约 512 tokens，故 adapter 必须显式截断并记录；Ollama 提供的 Qwen3 4B 量化包约 2.5 GB，但实际显存还包含 KV cache 和运行时开销，应用只承诺 8192 token 的保守预算。

### 5.2 可选质量档

评测若显示 Dense 召回不足，可切换 `BAAI/bge-m3`；Reranker 可切换 `BAAI/bge-reranker-v2-m3`。这两个模型的官方模型卡分别说明了 BGE-M3 的 1024 维/8192 token 能力以及 v2-m3 reranker 的多语言定位。Embedding 切换必须全量重建，不能复用 768 维索引。

### 5.3 OpenCode 的接入边界

OpenCode 支持自定义 OpenAI-compatible provider，也能发现本机 Ollama。项目不调用 OpenCode CLI、不解析它的 credential store，也不把 OpenCode 当模型网关。推荐配置方式是：

```text
OpenCode ─┐
          ├── same Ollama / compatible endpoint
Paper RAG ┘
```

这允许用户在两个工具中选择同一个已下载模型，同时避免私有配置耦合。若未来确实要通过 OpenCode Console inference，则只需新增一个兼容 endpoint 配置，不改变 `LLMProvider`。

## 6. 关键技术决策

| 决策 | 选择 | 放弃/延后 | 理由 |
| --- | --- | --- | --- |
| 运行形态 | 单机单用户 | 多租户 SaaS | 符合目标设备并缩小安全范围 |
| API | FastAPI | Django/Node | Python 模型生态与异步接口匹配 |
| Job | ARQ + Redis | Celery | 单机足够、组件更少 |
| 事实存储 | PostgreSQL | Redis/文件 JSON | 事务、约束、迁移和调试更可靠 |
| Dense index | FAISS CPU FlatIP | GPU FAISS/向量 DB | 数据规模可控、精确、部署简单 |
| Sparse index | 自研 BM25 | Elasticsearch | 支持公式、可复现、依赖轻 |
| Fusion | RRF | 原始 score 加权 | 避免量纲不可比 |
| Streaming | SSE | WebSocket | 当前仅需 server-to-client token stream |
| 前端 | React + Vite | Next.js | 无 SSR 需求，本地应用更轻 |
| PDF | PyMuPDF 文本解析 | OCR/Docling 全链路 | MVP 不要求复杂版面 |
| LLM | OpenAI-compatible adapter | 绑定某 SDK | 可复用本地和远端 provider |
| 索引更新 | 影子快照 + 原子切换 | active 原地写 | 保证失败不破坏可用索引 |

## 7. 实施阶段

每个阶段结束必须产生可运行、可测试的增量；禁止一次生成全部骨架后长期无法闭环。

### Phase 0：仓库和质量门

交付：

- Python/前端工程、锁文件、配置加载、结构化日志；
- Docker Compose 的 PostgreSQL/Redis；
- `/health/live`、`/health/ready`；
- Ruff、mypy、pytest、前端 lint/typecheck/test/build；
- CI 执行不依赖 GPU 的完整质量门。

验收：全新 clone 按 README 能启动基础服务；缺失环境变量有明确错误。

### Phase 1：数据模型与迁移

交付：Document、Collection、association、IngestionJob、DocumentVersion、IndexSnapshot、SystemState、Chunk、Session、Message、RetrievalLog；Alembic 初始迁移；repository/service transaction boundary。

验收：迁移可向上执行；外键、唯一约束、cascade 与枚举状态测试通过。

### Phase 2：上传与异步 ingestion 纵向切片

交付：流式上传、类型校验、文件 hash、job/outbox、ARQ worker、状态查询、retry；先以 fake parser/index 完成端到端状态机。

验收：上传返回 202；进度单调；worker 崩溃后可重试；重复 job 不产生重复数据。

### Phase 3：三种 Loader 与统一文档模型

交付：Registry、PDF、DOCX、Markdown Loader；normalization；OCR_REQUIRED 检测；fixture。

验收：三种 fixture 输出同一结构；段落顺序、page/line 和表格/代码类型符合 golden files。

### Phase 4：确定性 Chunking

交付：sentence splitter、Markdown element parser、heading tree、parent merge、title/table/chapter chunk、hash 和 metadata。

验收：边界 golden tests；同输入和配置重复执行产生相同 chunk_index/content_hash。

### Phase 5：Dense 索引闭环

交付：Embedding protocol、E5 adapter、模型 manifest、FAISS wrapper、faiss_id mapping、save/load、shadow activation。

验收：fake embedding 单测；真实 E5 smoke test；重启后 top-k 一致；维度不匹配被拒绝。

### Phase 6：BM25 与混合检索

交付：中英文 analyzer、BM25 statistics/snapshot、Dense/Sparse retrievers、RRF、统一 `RetrievalResult`、scope filter。

验收：手算 BM25 fixture 一致；术语和数字不丢失；Collection/document scope 无越界结果。

### Phase 7：Rerank、去重和上下文工程

交付：Reranker protocol、BGE adapter、降级策略、content hash dedup、neighbor/chapter expansion、token-budget builder、citation source map。

验收：无 GPU 使用 fake 完整运行；真实模型 smoke test；OOM batch 回退只有一次；packing 不超预算。

### Phase 8：LLM、多轮与引用

交付：OpenAI-compatible provider、rewrite、generation、prompt、citation validator、Session/Message、非流式 chat、SSE。

验收：fake provider 覆盖正常、超时、断流、无效引用；Ollama 或其他兼容 endpoint smoke test；SSE 事件顺序满足规格。

### Phase 9：删除、重建与一致性恢复

交付：document delete/reindex、snapshot compaction、startup verification、reconciliation task、degraded health。

验收：删除后所有 scope 不再返回文档；清理失败可重试；reindex 失败仍能使用旧 DocumentVersion 和 active IndexSnapshot 检索。

### Phase 10：前端 MVP

交付：Collection/论文侧栏、拖拽上传、任务状态、重试/删除/重建、chat stream、source drawer、retrieval debug 页面。

验收：核心路径 E2E；键盘可操作；loading/empty/error/disabled 状态完整；小屏不要求移动端原生体验，但不得横向溢出。

### Phase 11：Evaluation 与消融

交付：50+ 人工标注问题、CLI runner、指标、原始 JSON、Markdown report、配置/commit/seed manifest。

验收：五组规定实验可在同一数据集重复运行；报告可追溯每个错误案例。

### Phase 12：发布文档与验收

交付：README、架构说明、检索设计、评测报告、演示数据和故障排查；完整 DoD checklist。

验收：清空本地数据后按 Quick Start 重建并完成一次上传问答；所有质量门通过。

## 8. 工作包与依赖

```text
P0 repo
 └─ P1 schema
     └─ P2 ingestion state machine
         ├─ P3 loaders
         │   └─ P4 chunking
         │       ├─ P5 dense
         │       └─ P6 sparse + fusion
         │           └─ P7 rerank/context
         │               └─ P8 chat/citation
         └─ P9 lifecycle/recovery
P3 API contracts ───────────────└─ P10 frontend
P5..P8 ─────────────────────────── P11 evaluation
```

前端可在 API schema 固定后使用 mock server 并行开发，但 MVP 验收必须连接真实后端。Evaluation fixture 可从 loader/chunking 稳定后开始标注，避免 chunk id 频繁变化。

## 9. 风险与缓解

| 风险 | 影响 | 缓解与触发条件 |
| --- | --- | --- |
| RTX 2060 显存不足 | OOM、延迟抖动 | GPU semaphore=1、FP16/Q4、保守 batch、可卸载模型；评测后再升级模型 |
| PDF 文本顺序错误 | 引用内容错乱 | 坐标排序、golden fixture、显示 OCR/复杂版面非支持提示 |
| 模型替换导致索引不兼容 | 搜索崩溃或静默错误 | DocumentVersion/IndexSnapshot 保存 signature、dimension、revision、prefix；不兼容时拒绝加载并全量重建 |
| FAISS/BM25 与 DB 不一致 | 错引、漏检 | shadow snapshot、manifest hash、atomic activation、启动校验 |
| BM25 增量统计错误 | 排名漂移 | 按当前 corpus 重算统计，手算 fixture 和 rebuild-vs-incremental 对照 |
| SSE 中断产生错误消息 | 假完整答案 | interrupted 状态、上游取消、done/error 互斥 |
| 本地小模型幻觉 | 错答 | 严格 evidence prompt、引用校验、证据不足回答、可换远端模型 |
| 评测数据偏差 | 改进结论不可信 | 50+ 人工标注、错误分类、保存原始结果、禁止只报告单指标 |
| 无鉴权却监听公网 | 数据泄露 | 默认 loopback、CORS allowlist、README 明示风险 |

## 10. 性能预算

性能目标以本地开发体验为导向，不能牺牲正确性。用 RTX 2060 和 10 篇普通论文的基准数据测量：

- 非模型 API p95 < 300 ms；
- 搜索不含 rewrite p95 < 3 s；
- chat 首个 token 的目标 < 10 s，属于软目标，受本地 LLM 和模型换载影响；
- 50 页文本型 PDF 从上传到 ready 目标 < 3 min；
- worker 常驻显存不得无界增长，连续处理 20 个任务后显存回到稳定区间；
- 并发策略优先排队，不以 OOM 换吞吐。

这些值必须通过 benchmark 校准；未达到软目标不等于功能失败，但必须记录基线和瓶颈。数据完整性、引用正确性和可恢复性是硬要求。

## 11. 测试与评审策略

- 每个 Phase 的 API 或数据模型先写契约测试；
- 解析和 Chunking 使用 golden test，评审 diff 可直接看输入输出变化；
- 索引实现同时测试从零 rebuild 与增量操作结果等价；
- 模型 adapter 必须有 fake 和真实 smoke 两层，CI 只运行 fake；
- 任何 retrieval 默认值变化必须附带相同数据集的 before/after 指标；
- PR 评审优先检查数据丢失、索引错配、scope 越界、引用映射和显存并发。

## 12. 交付物

MVP 最终仓库至少包含：

- 可运行 API、worker、frontend 和 compose 基础设施；
- Alembic migrations 与 lockfiles；
- 三种 Loader、Chunking、Dense/BM25/RRF/Rerank/Context/LLM/Citation；
- Collection、Session、Document lifecycle；
- 单元、集成、E2E、真实模型 smoke tests；
- 50+ eval dataset、原始结果和消融报告；
- README、spec、proposal、architecture、retrieval-design、evaluation、故障排查；
- 模型与索引 manifest，不提交模型权重、上传论文、密钥或运行时索引。

## 13. 推迟到 P1/P2 的能力

P1：OCR、复杂 PDF parser 实验、JSON/HTML/CHM/XLSX Loader、可配置同义词/术语词典、响应缓存、答案质量 judge、Collection 批量操作，以及论文摘要/方法/实验/贡献/局限五类结构化阅读预设。  
P2：多用户权限、向量数据库、agent、知识图谱、联网文献搜索、论文对比工作流、云部署。

结构化阅读预设只能复用主 Retrieval Engine 并改变 query/prompt template，不得复制 ingestion、index 或 citation 实现。

任何推迟项进入实现前必须先更新 spec；不得以“顺手实现”绕过范围控制。

## 14. 启动开发前检查

- [ ] `docs/spec.md` 和本提案已被实现者完整阅读；
- [ ] RTX 2060 主机的 CUDA/PyTorch 兼容版本已通过最小 smoke test；
- [ ] embedding/reranker revision 已解析并固定；
- [ ] LLM endpoint 与 model 已通过 `/v1/chat/completions` 测试；
- [ ] PostgreSQL、Redis 和 storage 目录准备完毕；
- [ ] 示例论文许可允许保存在测试 fixture 中；
- [ ] Phase 0 质量门先于功能实现合入。

## 15. 依据

- E5 模型配置与许可：[multilingual-e5-base](https://huggingface.co/intfloat/multilingual-e5-base)
- BGE reranker 配置与许可：[bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base)
- BGE-M3 维度与上下文：[bge-m3](https://huggingface.co/BAAI/bge-m3)
- 多语言质量档 reranker：[bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- Ollama 兼容接口：[OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- OpenCode provider 边界：[Providers](https://opencode.ai/docs/providers/)

## 16. 2026-08-25 实现审计与恢复计划

实际 Windows + PostgreSQL + Redis + ARQ 验证证明，历史 “Phase 0–12 complete” 结论不成立：生产 Worker 默认使用 fake parser/chunker；真实论文被标记为 `ready` 但只有 1 页、0 chunks；索引仅构建单文档 FAISS，未构建全局 FAISS/BM25 snapshot；search/session/chat routes 未挂载；private benchmark 不能生成真实 predictions。

恢复实施按以下纵向切片重新验收，完成前不得恢复 “MVP complete” 状态：

1. R1 Production ingestion：真实 Loader、Chunker、Embedding registry 和 storage wiring；非空文档不得 0 chunks；
2. R2 Corpus snapshot：从全部 active DocumentVersion 重建 FAISS + BM25 + manifest，影子校验后原子激活；
3. R3 Retrieval API：scope validation、Dense/BM25、RRF、可选 rerank、debug trace；
4. R4 Session/Chat API：持久化消息、context/citation、OpenAI-compatible provider、SSE 生命周期；
5. R5 Frontend contract：前端真实调用上述 API，覆盖 loading/empty/error/success；
6. R6 Evaluation：6 文档真实 ingestion、label resolver、60 问题 predictions、metrics 与错误分类；
7. R7 Release：全新环境 E2E、质量门、README/architecture/troubleshooting、提交推送。

每个恢复切片必须包含实现、契约测试、集成验证和 memory 记录；只完成类或纯函数不算完成。

## 17. PDF Ingestion V2 架构升级

2026-08-25 的私有 6 文档 / 60 问题验证中，52 个可回答样本仅 41 个能解析完整 evidence label。11 个 hard cases 中前 10 个主要由表格、版面、Unicode 和段落重建导致，第 11 个由多轮 query rewrite 与 table retrieval 导致。这不是继续增加 PDF 正则即可关闭的缺陷。

批准按 [`docs/pdf-ingestion-v2-spec.md`](./pdf-ingestion-v2-spec.md) 实施：项目自有 Canonical Document IR；PyMuPDF fast path；Docling layout parser；MinerU 隔离 challenger；table parent/row/group chunk；row-first retrieval + parent expansion；page/bbox/cell citation；结构化多轮 rewrite。

实施顺序固定为 V2-0 至 V2-8。完成门为 11/11 hard cases、52/52 answerable labels 和完整 60 题 baseline；任一门未通过不得声明 PDF Ingestion V2 完成。
