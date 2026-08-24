# Paper RAG Assistant 测试与评测实施方案

> 状态：Ready for implementation  
> 版本：1.0.0  
> 最后更新：2026-08-24  
> 规范来源：[`spec.md`](./spec.md)  
> 开发路线：[`proposal.md`](./proposal.md)

## 1. 交接任务

请为现有 Paper RAG Assistant 建立可重复、可分阶段运行的测试与评测系统。当前先实现测试框架、数据契约和评测 runner；用户稍后提供论文后，再制作正式小型数据集。

必须交付：

1. 单元、集成、API/SSE 契约和 E2E 测试方案；
2. RTX 2060 真实模型 smoke test；
3. 论文评测数据集 schema、校验器和标注流程；
4. Retrieval、Citation、Answer 与性能指标；
5. Dense、BM25、Hybrid、Rerank、Full Pipeline 消融 runner；
6. JSON 原始结果、环境 manifest 和 Markdown 报告。

本文件不授权修改 `spec.md` 的产品行为。发现实现与规范不一致时，应报告差异或先更新规范，禁止让测试迁就错误实现。

## 2. 当前仓库状态

截至 2026-08-24：

- Phase 0 已完成：Python/uv、React/Vite、Compose、health、质量门和 CI；
- Phase 1 已完成：ORM、Alembic 和初始迁移；
- Phase 2 已完成：上传、Collection、Job、ARQ ingestion 状态机；
- Phase 3 已完成：PDF、DOCX、Markdown Loader；
- Phase 4 已完成：确定性 Chunking；
- 后端已有 116 个通过的测试，3 个依赖 live PostgreSQL 的集成测试暂时 skipped；
- Phase 5 的 Embedding、FAISS 和 snapshot 代码正在开发，工作树存在未提交修改；
- Docker 尚未在当前 Mac 安装，live PostgreSQL/Redis 集成测试尚未实跑；
- 目标部署设备为 NVIDIA RTX 2060，而当前开发机是 macOS arm64；
- PyMuPDF 1.28.2 在当前 macOS pytest 进程内存在 segfault，现有 PDF 测试通过 subprocess 隔离。

开始前必须运行：

```bash
git status --short
git log -5 --oneline
uv run pytest -q
```

禁止覆盖或回退当前未提交的 Phase 5 文件，尤其是：

```text
app/embedding/
app/index/faiss_index.py
app/index/snapshot.py
app/services/ingestion.py
tests/unit/test_embedding.py
tests/unit/test_faiss_index.py
tests/unit/test_fake_embedding.py
tests/unit/test_ingestion_embedding.py
tests/unit/test_snapshot.py
```

## 3. 背景

本项目的质量不能以“页面能够聊天”判断。完整链路是：

```text
Document ingestion
-> parsing
-> deterministic chunking
-> Dense/BM25 indexing
-> hybrid retrieval
-> reranking
-> context packing
-> LLM generation
-> citation validation
```

各层都可能造成最终错误：

- Loader 打乱段落、页码或表格；
- Chunking 截断定义、标题或实验数据；
- Dense 漏掉术语、缩写和数字；
- BM25 漏掉同义表达和跨语言问题；
- Fusion 或 rerank 把正确候选降权；
- Context packing 丢失证据或打乱引用；
- LLM 生成无来源事实；
- Reindex/delete 造成数据库与检索快照不一致。

因此测试必须能够定位错误阶段，评测必须保存中间候选，禁止只测最终答案。

## 4. 测试左移策略

Evaluation 不应等到 Phase 11 才开始：

```text
Phase 0-4  -> 已有单元测试和 Loader/Chunking fixtures
Phase 5    -> 补 Dense/index snapshot 测试
Phase 6    -> 建 20-30 问题 Retrieval smoke dataset
Phase 7-8  -> 在同一数据集上增加 Rerank、Citation、Answer 指标
Phase 9-10 -> 生命周期恢复和前端 E2E
Phase 11   -> 扩展到 50-100 问题，冻结 test split
```

任何改变 Chunk 边界、analyzer、embedding、fusion、rerank、rewrite 或 context packing 的变更，都必须附带相同数据集上的 before/after 结果。

## 5. 测试分层

### 5.1 Layer A：单元测试

单元测试不依赖网络、数据库、Redis、GPU 或真实模型，必须在 CI 稳定运行。

| 模块 | 必测内容 |
| --- | --- |
| Loader Registry | 大小写扩展名、未知格式、注册解耦 |
| Normalization | CRLF、NUL、空白、行号、Unicode |
| SentenceSplitter | 中英文标点、6 句、800 字符、超长单句 |
| Heading Tree | 正常层级、跳级、虚拟 root、section path |
| Table Chunk | header 重复、完整 row、单行超长 |
| Chunk Metadata | index、hash、parent/chapter、raw/retrieval content |
| E5 Adapter | query/passage 前缀、截断、normalize、零向量 |
| FAISS Wrapper | dimension、add/search、save/load、重复 ID |
| Snapshot | manifest hash、临时构建、原子激活、损坏恢复 |
| BM25 | tf/df/idf、数字和术语、增删统计 |
| RRF | rank 从 1 开始、缺失候选、稳定 tie-break |
| Scope | all/documents/collection、非法组合、非 ready 文档 |
| Dedup | 先 chunk id，再 content hash |
| Expansion | rerank 后执行、同文档同 section、边界 |
| Context | token budget、合并、截断、source marker |
| Citation | 有效、重复、越界、缺失 marker |
| State Machine | ingest/reindex/delete 合法和非法迁移 |

Chunking、BM25、RRF、Context Builder 的语句覆盖率目标不低于 90%，后端整体目标不低于 80%。优先使用参数化测试、golden fixture 和手算样例。

### 5.2 Layer B：集成测试

使用临时 PostgreSQL、Redis 和独立临时 storage。除 model smoke 外，使用 deterministic fake embedding、reranker 和 LLM。

必须覆盖：

- 上传 -> Document/Job -> ARQ worker -> ready；
- parsing、embedding、indexing 阶段失败后的状态和 retry；
- 同一文档并发 reindex 只有一个 writer；
- DocumentVersion 与 IndexSnapshot 影子构建和原子激活；
- reindex 失败后旧 snapshot 仍可检索；
- 删除先退出 scope，再清理 DB、Collection、FAISS、BM25 和文件；
- FAISS/BM25 save/load/restart 后结果一致；
- manifest 损坏时 readiness degraded；
- Collection scope 在 Dense 和 BM25 两路同时生效；
- reranker 不可用时降级为 RRF 并报告原因；
- LLM 超时、断流、无效引用时 Message/Log 状态正确。

集成测试不得读取或污染用户真实 `storage/`。

### 5.3 Layer C：API 与 SSE 契约测试

依据 `/api/v1` 规范测试：

- HTTP status、稳定错误码、snake_case schema；
- 扩展名、magic bytes、100 MiB 上传限制；
- cursor pagination 和稳定排序；
- documents、jobs、collections、sessions CRUD；
- search scope、top_k、minimum_should_match、debug；
- 非流式和流式 chat 语义一致；
- SSE 顺序：`meta -> sources -> delta* -> done|error`；
- `done` 与 `error` 互斥；
- heartbeat、客户端断开和上游取消；
- 响应及日志不泄露绝对路径、prompt 或 key。

保存 OpenAPI schema snapshot；破坏性 schema 变化必须先更新 spec。

### 5.4 Layer D：真实模型 smoke test

使用 `model_smoke` marker，只在 RTX 2060 主机显式运行，不进入普通 CI。

检查：

1. CUDA device 正确；
2. model ID/revision 与 manifest 一致；
3. embedding dimension 正确并完成 L2 normalize；
4. 中英文 query/passage 相似度顺序合理；
5. reranker 能对 4 个候选正确排序；
6. embedding/rerank 起始 batch 16/4 不 OOM；
7. OOM 时只减 batch 重试一次；
8. OpenAI-compatible endpoint 支持非流式和流式；
9. 连续运行 20 次后显存没有持续增长；
10. 记录 latency 与 peak GPU memory。

每次记录 GPU、显存、driver、CUDA、PyTorch、model revision、batch 和输入长度，不能只报告“加载成功”。

### 5.5 Layer E：前端 E2E

使用 Playwright 或等价工具，后端使用 deterministic fake 模型：

- 创建 Collection；
- 上传三种文档并观察状态；
- 失败任务 retry；
- 切换 document/collection scope；
- chat stream；
- 点击引用展开文档、section、page 和 content；
- delete/reindex 确认；
- loading、empty、error、disabled 状态；
- 核心操作可键盘完成。

E2E 不依赖公网或用户真实论文。

## 6. 论文评测数据集

### 6.1 用户输入

正式数据集需要用户提供：

```text
- 3–5 篇文本型 PDF
- 提问语言：中文 / 英文 / 双语
- 重点：方法 / 实验 / 对比 / 均衡
- 是否允许本地保存证据摘录
- 用户已有问题（可选）
```

论文默认放在不提交 Git 的 `eval/private_corpus/`。如果版权不允许分发，dataset、evidence 和报告也保持本地。

### 6.2 建议规模

第一轮制作约 60 个问题：

| 类型 | 数量 | 目的 |
| --- | ---: | --- |
| 事实与定义 | 10 | 术语、模型名、数据集和精确值 |
| 方法与机制 | 10 | 语义检索与章节定位 |
| 实验与表格 | 10 | 数字、指标和表格 header |
| 跨章节综合 | 8 | 多证据 Context packing |
| 跨论文比较 | 8 | 多文档 scope 与冲突处理 |
| 多轮指代改写 | 6 | standalone query rewrite |
| 困难负例/不可回答 | 8 | 拒答和 scope 边界 |

60 个问题用于 MVP benchmark，足以发现回归，但不用于宣称普遍效果。

### 6.3 标注流程

1. 计算论文 SHA-256，登记 title、language 和 usage note；
2. 使用稳定 Loader/Chunker 生成 DocumentVersion；
3. AI 生成候选问题、答案和证据；
4. 人工检查自然性、答案唯一性与证据充分性；
5. 人工确认不可回答问题在 scope 内确实无答案；
6. 保存稳定 evidence anchor；
7. 针对当前 IndexSnapshot 自动解析 relevant chunk IDs；
8. 按类型分层，70% 为 dev、30% 为冻结 test；
9. 调参只看 dev；最终报告才运行 test；
10. reindex 后重新解析 chunk IDs，但禁止修改冻结问题迎合结果。

### 6.4 稳定证据锚点

不能只人工标注 Chunk ID，因为 Chunk ID 会随 reindex 改变。事实标注使用：

```text
document_sha256
+ page/section_path
+ exact_quote
+ normalized_quote_hash
```

`relevant_chunk_ids` 是特定 IndexSnapshot 的派生标注。quote 跨多个 Chunk 时允许映射多个相关 ID。

### 6.5 数据格式

`eval/dataset.json`：

```json
{
  "dataset_version": "1.0.0",
  "documents": [
    {
      "document_key": "paper-001",
      "sha256": "...",
      "title": "...",
      "language": "en",
      "usage_note": "private-local-evaluation"
    }
  ],
  "samples": [
    {
      "id": "eval-001",
      "split": "dev",
      "question_type": "experiment",
      "question": "该方法在数据集 X 上的 FID 是多少？",
      "scope": {
        "type": "documents",
        "document_keys": ["paper-001"]
      },
      "answerable": true,
      "reference_answer": "...",
      "evidence": [
        {
          "document_key": "paper-001",
          "page": 7,
          "section_path": ["Experiments", "Comparison"],
          "quote": "...",
          "quote_hash": "..."
        }
      ],
      "snapshot_labels": {
        "index_snapshot_id": "...",
        "relevant_chunk_ids": ["..."]
      },
      "notes": ""
    }
  ]
}
```

不可回答问题必须设置 `answerable=false`、`reference_answer=null`、`evidence=[]`。

多轮样本增加 `conversation` 和 `expected_standalone_query`，改写质量以实体保留、语义保持及 rewrite on/off 的 Retrieval 差异衡量，不能只用字符串完全相等。

## 7. 指标

### 7.1 Retrieval

- Recall@1/3/5/10；
- MRR；
- nDCG@5/10；
- 按 question_type 的 macro 指标；
- Dense/BM25 candidate overlap；
- retrieval/rerank latency；
- 多证据问题的 all-evidence coverage。

### 7.2 Citation

- Citation Precision；
- Citation Recall；
- Citation Validity；
- Citation Completeness。

### 7.3 Answer

- Answerable Accuracy；
- Unanswerable Rejection Accuracy；
- 数字/名称题的 normalized match；
- 人工 Correctness、Faithfulness、Completeness，各 1–5；
- 可选 LLM judge，但必须记录 judge model/revision/prompt，并人工抽检。

LLM judge 不能作为唯一真值。

### 7.4 性能

- 各 pipeline stage latency；
- search p50/p95；
- time-to-first-token p50/p95；
- peak GPU memory；
- 连续任务成功率；
- 重启后的 snapshot 恢复时间。

## 8. 初始质量门

第一轮先建立 baseline，再冻结正式 gate：

| 指标 | 初始建议 | 类型 |
| --- | ---: | --- |
| Recall@10 | >= 0.85 | 候选硬门槛 |
| MRR | >= 0.60 | 观察指标 |
| Citation Precision | >= 0.95 | 候选硬门槛 |
| Citation Recall | >= 0.85 | 候选硬门槛 |
| Unanswerable rejection | >= 0.80 | 候选硬门槛 |
| Search p95 | < 3 s | RTX 2060 软目标 |
| Chat 首 token p95 | < 10 s | RTX 2060 软目标 |

同一 frozen test 上 Recall@10 或 Citation Precision 下降超过 2 个百分点时，默认阻止合并，除非用户批准清晰记录的质量/性能取舍。

报告必须按 question type 展示结果，避免简单题掩盖表格、综合和跨论文问题的失败。

## 9. 消融矩阵

| Run | Dense | BM25 | RRF | Rerank | Rewrite | Expansion |
| --- | --- | --- | --- | --- | --- | --- |
| A | on | off | off | off | off | off |
| B | off | on | off | off | off | off |
| C | on | on | on | off | off | off |
| D | on | on | on | on | off | off |
| E | on | on | on | on | on | on |

可选：E-no-rewrite、E-no-expansion、不同 chunk size、不同 rerank top-k、E5 与 BGE-M3 profile。

模型对比必须建立各自 IndexSnapshot，禁止只替换模型名称。所有 run 必须保存完整配置。

## 10. 目录与交付物

```text
eval/
├── README.md
├── dataset.schema.json
├── dataset.example.json
├── dataset.json                 # 可私有
├── private_corpus/              # gitignored
├── manifests/
├── labeling/
│   ├── generate_candidates.py
│   ├── validate_evidence.py
│   └── resolve_chunk_labels.py
├── runners/
│   ├── retrieval_eval.py
│   ├── answer_eval.py
│   └── ablation.py
├── metrics/
│   ├── retrieval.py
│   ├── citation.py
│   └── answer.py
└── reports/
    ├── raw/
    └── summary.md

tests/
├── unit/
├── integration/
├── contract/
├── e2e/
├── model_smoke/
└── fixtures/
```

必须交付 dataset schema/example、evidence validator、chunk label resolver、metrics、ablation runner、raw JSON、summary Markdown 和环境 manifest。

## 11. 报告格式

`eval/reports/summary.md` 至少包含：

1. dataset version、论文数、问题数和分类分布；
2. Git commit、lock hash、model revision、IndexSnapshot；
3. Chunk、BM25、RRF、rerank、rewrite 和 context 参数；
4. 总体及分类 Retrieval 指标；
5. Citation/Answer 指标；
6. latency 和 GPU memory；
7. 消融表；
8. Top failure cases；
9. parse/chunk/retrieve/rerank/context/generate/citation 错误归因；
10. 下一步建议及验证方式。

禁止只输出单个平均分或只展示成功案例。

## 12. 实施任务

### Task 1：补齐现阶段测试

- 保留当前 Phase 5 工作；
- 完成 embedding、FAISS、snapshot 和 ingestion-index integration 测试；
- 建立 `model_smoke` marker；
- 不要求下载真实模型运行普通 CI。

验收：fake adapter 下 Phase 5 完整通过，损坏 snapshot 和维度错配会明确失败。

### Task 2：测试基础设施

- pytest markers：unit、integration、contract、e2e、model_smoke；
- 临时 PostgreSQL/Redis/storage fixtures；
- deterministic fake embedding/reranker/LLM；
- CI 默认排除 model_smoke。

验收：无网络无 GPU 环境稳定运行。

### Task 3：评测框架

- JSON Schema、loader、validator；
- evidence anchor 与 snapshot label resolver；
- Retrieval/Citation/Answer metrics；
- runner 和 Markdown report；
- 使用虚构 `dataset.example.json` 跑通。

验收：一条命令生成 raw JSON、summary 和 environment manifest。

### Task 4：论文数据集

等待用户提供论文后，生成候选问题，完成人工复核、evidence anchoring、分层 split 和 chunk label resolution。

验收：50+ 已验证样本；answerable 样本都有证据；negative 已人工确认。

### Task 5：Baseline 与冻结

- 运行五组消融；
- 根据实际结果确定 gate；
- 冻结 test split 和 dataset 1.0.0。

验收：报告可复现，后续变更可直接比较。

## 13. Definition of Done

只有同时满足以下条件才算完成：

1. 单元、集成、契约、E2E、model smoke 分层清晰；
2. 普通 CI 不需要 GPU、网络或真实模型；
3. RTX 2060 smoke 有环境和性能记录；
4. 数据集使用稳定 evidence anchor；
5. relevant chunk labels 与 IndexSnapshot 绑定；
6. Retrieval、Citation、Answer 和性能指标全部实现；
7. 五组消融可由统一 runner 执行；
8. raw results、Markdown report、environment manifest 同时生成；
9. 每个失败样本能归因到 pipeline stage；
10. 50+ 样本人工复核且 test split 冻结；
11. 没有提交用户私有论文、证据或密钥；
12. README 提供完整运行命令。

## 14. 当前可做与必须等待

现在可以完成 Task 1–3。Task 4–5 必须等待用户提供论文和标注偏好。

在没有用户论文时，不要从网络任意下载论文冒充正式数据集；只使用许可清晰的小 fixture 或虚构 example 验证框架。

