# Paper RAG Assistant 技术规格

> 状态：Approved for implementation（实现重新验收中）
> 版本：1.1.0
> 最后更新：2026-08-25
> 目标环境：单机、单用户、NVIDIA RTX 2060（按 6 GB 显存预算）

## 1. 文档约定

本文是实现、测试和验收的规范性来源。关键词“必须”“禁止”“应该”“可以”分别表示强制要求、强制约束、默认要求和可选能力。若本文与 `proposal.md` 冲突，以本文为准；若代码改变了已批准行为，必须先更新本文。

所有长度配置若无特别说明均使用 Unicode 字符数。模型输入限制、上下文预算和 `token_count` 使用对应模型 tokenizer 的 token 数，两者禁止混用。

## 2. 已确认的产品决策

- MVP 是单机、单用户、本地优先的论文知识库，不实现登录、租户或角色权限。
- MVP 支持文本型 PDF、DOCX 和 Markdown；扫描 PDF OCR、复杂公式恢复、跨页表格重建不在 MVP 范围。
- 上传后的解析、切片和建索引由 Redis + ARQ worker 异步执行。
- 前端使用 React + Vite + TypeScript，界面为浅色、克制、Apple 风格。
- Collection 是用户可管理的论文集合，一篇文档可以属于多个 Collection。
- 必须支持删除、重新索引和失败任务重试，并保证数据库、索引与文件的一致性。
- 模型必须可配置、可下载、可替换；业务层禁止依赖具体模型 SDK。
- 回答模型通过 OpenAI-compatible Chat Completions 接口接入。Ollama、LM Studio、vLLM 或远端兼容服务都可作为后端。
- OpenCode 不是运行时依赖。它与本系统可以指向同一个 OpenAI-compatible/Ollama endpoint 和 model，但本系统不得读取 OpenCode 的私有凭据文件。

## 3. 目标与非目标

### 3.1 MVP 目标

用户必须能够完成以下闭环：

1. 创建 Collection；
2. 上传 PDF、DOCX 或 Markdown；
3. 查看解析和索引状态；
4. 在一篇、多篇或一个 Collection 范围内检索；
5. 发起多轮问答并接收 SSE 流式响应；
6. 查看答案引用的文档、章节、页码和原始 Chunk；
7. 删除或重新索引文档；
8. 查看 Dense、BM25、RRF、Rerank 和最终上下文的调试结果；
9. 使用不少于 50 个标注问题运行检索评测和消融实验。

### 3.2 MVP 非目标

- 多用户、组织、权限、云同步和公网 SaaS；
- OCR、手写识别、复杂数学公式语义还原；
- 对跨页复杂表格做完美结构恢复；
- Agent、自主联网搜索、知识图谱；
- CHM、HTML、JSON、XLSX Loader；
- 分布式向量数据库和多节点高可用；
- 自动生成 50 条高质量人工标注评测数据。

论文摘要、方法分析、实验分析、贡献分析和局限分析属于 P1 的“结构化阅读预设”。它们必须复用同一个 Retrieval Engine，只增加受版本控制的 query/prompt template；MVP 不为它们建立另一套索引或专用数据链路。

## 4. 用户故事与验收

| ID | 用户故事 | 验收结果 |
| --- | --- | --- |
| US-01 | 上传论文 | API 立即返回 `202`、`document_id` 和 `job_id`，后台状态可查询 |
| US-02 | 管理论文集合 | 可增删改查 Collection，并可向集合添加或移除文档 |
| US-03 | 查看处理结果 | 文档详情展示状态、错误、页数、字符数和 Chunk 数 |
| US-04 | 精确检索 | 搜索结果可回溯到唯一 `chunk_id`、section 和 page |
| US-05 | 论文问答 | 回答仅基于打包上下文，证据不足时明确说明 |
| US-06 | 查看引用 | `[1]` 等 marker 能展开为唯一来源，不存在悬空引用 |
| US-07 | 多轮追问 | 指代问题先改写为独立问题，日志保留原问题和改写结果 |
| US-08 | 运维数据 | 删除、重试和重建索引具有明确状态且失败可诊断 |
| US-09 | 评测 | 输出可复现的 JSON 与 Markdown 报告，包含配置快照 |

## 5. 技术基线

### 5.1 后端

- Python 3.12；
- FastAPI + Pydantic v2；
- SQLAlchemy 2.x + Alembic；
- PostgreSQL 16；
- Redis 7 + ARQ；
- PyMuPDF（文本型 PDF）、python-docx、markdown-it-py；
- sentence-transformers / transformers + PyTorch CUDA；
- FAISS CPU `IndexIDMap2(IndexFlatIP)`；RTX 2060 用于模型推理，MVP 不要求 FAISS GPU；
- 自研可持久化 Okapi BM25；中文分词 jieba，英文使用确定性的正则 tokenizer。

依赖必须锁定在 lockfile 中。模型 revision 必须固定在配置或首次下载形成的 manifest 中，禁止生产索引跟随浮动的 `main` 静默变化。

### 5.2 前端

- React 18+、Vite、TypeScript strict；
- React Router；
- TanStack Query；
- 原生 CSS variables 或 CSS Modules；除非提案更新，不引入大型 UI 框架。

主界面为左侧论文/集合列表与右侧阅读问答区。使用白色、浅中性灰、近黑文字和少量系统蓝；禁止把绿色作为主色，避免过大标题、重度渐变和装饰性玻璃卡片。

### 5.3 模型配置

模型由 `ModelProfile` 管理。默认档针对 RTX 2060：

| 用途 | 默认模型 | 关键约束 |
| --- | --- | --- |
| Embedding | `intfloat/multilingual-e5-base` | 768 维、最大 512 tokens、FP16、归一化；query/passages 必须添加 E5 前缀 |
| Reranker | `BAAI/bge-reranker-base` | 中英文 cross-encoder、FP16、输入最大 512 tokens、batch size 默认 4 |
| Generator | OpenAI-compatible；本地建议 `qwen3:4b-instruct` Q4 | endpoint、model、key 完全配置化；默认最大上下文预算 8192 tokens |
| Rewrite | 默认复用 Generator | temperature 0，JSON 结构化输出；失败时回退原问题 |

可选质量档：

| 用途 | 质量档模型 | 影响 |
| --- | --- | --- |
| Embedding | `BAAI/bge-m3` | 1024 维、长上下文、多语言；需要新 model signature、DocumentVersion 和全量 IndexSnapshot 重建 |
| Reranker | `BAAI/bge-reranker-v2-m3` | 约 0.6B 参数，多语言；延迟和显存更高 |

模型替换规则：

1. Embedding 模型 ID、revision、dimension、normalize、query prefix 或 pooling 任一变化，都必须生成新 model signature，为全部文档创建兼容的 `DocumentVersion`，并重建 `IndexSnapshot`；
2. 旧版本索引在新版本原子切换成功前必须可读；
3. Reranker 或 Generator 可热更新，不要求重建向量索引，但日志必须记录版本；
4. CUDA OOM 时 worker 只可自动减小 batch 一次并重试；再次失败必须标记任务失败，不得静默转为截断更多内容；
5. 本地 Generator 与检索模型共享 6 GB 显存时必须串行化 GPU 重任务。允许卸载空闲模型，禁止无界并发。

## 6. 系统架构

```text
Browser
  │ REST + SSE
  ▼
FastAPI ───────── PostgreSQL
  │ enqueue            │ metadata / chat / logs
  ▼                    │
Redis / ARQ             │
  │                     │
  ▼                     │
Worker ───── uploads / FAISS snapshots / BM25 snapshots
  │
  ├── document parsers
  ├── chunking pipeline
  ├── embedding + indexing
  └── retrieval + reranker

FastAPI ── OpenAI-compatible LLM endpoint
```

### 6.1 进程职责

- API：输入校验、事务、任务投递、查询、SSE 转发；禁止执行长时间文档解析或批量 embedding。
- Worker：解析、切片、索引、重试和一致性修复；同一文档同一时刻最多一个写任务。
- PostgreSQL：业务事实来源。FAISS/BM25 只保存可重建的检索快照。
- Redis：ARQ 队列、任务进度、短期会话缓存和可选响应缓存；禁止作为永久消息存储。
- LLM endpoint：仅负责 rewrite 和 generation，不直接访问数据库或文件。

## 7. 项目目录

```text
paper-rag-assistant/
├── app/
│   ├── main.py
│   ├── api/{dependencies,errors,documents,collections,search,chat,sessions,jobs}.py
│   ├── core/{config,logging,ids,security}.py
│   ├── db/{base,session}.py
│   ├── models/{document,document_version,collection,chunk,job,index_snapshot,system_state,session,message,retrieval_log}.py
│   ├── schemas/{common,document,collection,search,chat,session,job}.py
│   ├── loaders/{base,registry,pdf,docx,markdown}.py
│   ├── chunking/{models,sentence,markdown,heading_tree,pipeline}.py
│   ├── embedding/{base,e5,bge,registry}.py
│   ├── index/{base,faiss_index,bm25_index,snapshot,manager}.py
│   ├── retrieval/{models,dense,sparse,fusion,dedup,engine}.py
│   ├── rerank/{base,bge,registry}.py
│   ├── context/{expander,builder,citations}.py
│   ├── llm/{base,openai_compatible,prompts}.py
│   ├── services/{document,collection,retrieval,chat,index}.py
│   └── workers/{settings,tasks}.py
├── migrations/
├── frontend/src/{api,components,features,pages,styles,types}/
├── eval/{dataset.json,retrieval_eval.py,answer_eval.py,ablation.py}
├── tests/{unit,integration,e2e,fixtures}/
├── docs/{spec,proposal}.md
├── storage/{uploads,indexes,tmp}/
├── AGENTS.md
├── memory.md
├── docker-compose.yml
├── pyproject.toml
├── uv.lock
└── README.md
```

## 8. 标识、时间和通用字段

- 所有业务主键使用 UUIDv4，API 表示为小写带连字符字符串。
- 数据库时间使用 `TIMESTAMPTZ`，应用内部统一 UTC；API 输出 RFC 3339 UTC。
- 所有可变实体包含 `created_at`、`updated_at`。
- 枚举在数据库中使用受约束字符串；添加枚举值必须带迁移。
- 所有分页接口使用 cursor，默认 20，最大 100；排序必须稳定并以 `id` 作为次排序键。

## 9. 数据模型

### 9.1 Document

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| id | UUID | PK |
| filename | varchar(255) | 清理后的展示名 |
| stored_filename | varchar(255) | 随机文件名，禁止使用用户路径 |
| media_type | varchar(100) | allowlist |
| extension | varchar(10) | `pdf`、`docx`、`md` |
| title | text nullable | 解析标题，缺失时使用去扩展名文件名 |
| sha256 | char(64) | 原始文件 hash |
| file_size | bigint | 1..104857600 |
| status | varchar(24) | Document 状态机 |
| status_message | text nullable | 面向用户的安全错误摘要 |
| page_count | int nullable | PDF 页数；其他格式可为空 |
| character_count | int nullable | normalized content 字符数 |
| chunk_count | int | 默认 0 |
| active_document_version_id | UUID nullable | FK `document_versions.id`；成功激活后切换 |
| parser_version | varchar(100) nullable | 成功后写入 |
| created_at/updated_at | timestamptz | 必填 |

同一用户允许上传内容相同但文件名不同的文件；MVP 不做全局拒绝。单文档重新索引必须复用原文件和 document id。

### 9.2 Collection 与关联

`collections`：`id`、唯一 `name`（1..120）、`description`（0..1000）、时间字段。  
`collection_documents`：复合主键 `(collection_id, document_id)`、`created_at`。删除 Collection 只删除关联，不删除文档；删除文档级联删除关联。

### 9.3 IngestionJob

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK，同时作为对外 job id |
| document_id | UUID | FK |
| kind | enum | `ingest`、`reindex`、`delete_cleanup` |
| status | enum | `queued`、`running`、`succeeded`、`failed`、`cancelled` |
| stage | enum | `queued`、`parsing`、`chunking`、`embedding`、`indexing`、`finalizing` |
| progress | int | 0..100，单调不减 |
| attempt | int | 从 1 开始 |
| error_code | varchar nullable | 稳定机器码 |
| error_message | text nullable | 已脱敏 |
| started_at/finished_at | timestamptz nullable | 生命周期 |
| created_at/updated_at | timestamptz | 必填 |

### 9.4 Chunk

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | UUID | PK |
| document_id | UUID | FK，删除级联 |
| document_version_id | UUID | FK，表示本篇文档的一次解析/切片版本 |
| chunk_index | int | 文档内从 0 递增，版本内唯一 |
| kind | enum | `text`、`title`、`table`、`code`、`chapter` |
| parent_chunk_id | UUID nullable | 直接父 Chunk |
| chapter_chunk_id | UUID nullable | 所属章节 Chunk |
| section_path | JSON array[string] | 从顶层到当前标题 |
| page_start/page_end | int nullable | 1-based，满足 start <= end |
| line_start/line_end | int nullable | normalized 文档 1-based 行号 |
| raw_content | text | 尽量忠实的原内容，用于引用和 prompt |
| retrieval_content | text | 标题 + section + content，用于 embedding/BM25 |
| content_hash | char(64) | normalized `raw_content` 的 SHA-256 |
| character_count | int | `raw_content` 字符数 |
| token_count | int | Generator tokenizer 计数；模型改变可重算 |
| metadata | jsonb | loader 特有但可序列化的信息 |
| created_at | timestamptz | 必填 |

唯一约束：`(document_version_id, chunk_index)`。`document_id` 必须与 DocumentVersion 所属文档一致；`parent_chunk_id` 和 `chapter_chunk_id` 必须指向同一 DocumentVersion。代码块在 `code_not_add_index=true` 时仍可存储，但不进入检索索引。

源材料中的字段按以下方式规范化，禁止同时保存两套同义字段：`page_content -> retrieval_content`，`changed_prompt -> raw_content`；`retrieve_result_str` 是 API 展示层根据 RetrievalResult 派生的字符串，不持久化到 Chunk；`filename`、`title` 通过 Document 关系读取，不在每个 Chunk 冗余保存。

### 9.5 DocumentVersion 与 IndexSnapshot

`document_versions` 表示单篇文档的一次不可变解析/切片结果，保存 `id`、`document_id`、`status`、`parser_version`、`chunk_config`、`embedding_model_id`、`embedding_revision`、`embedding_dimension`、`embedding_signature`、`analyzer_config`、统计和时间字段。状态为 `building`、`ready`、`superseded`、`failed`。Chunk 只属于一个 DocumentVersion；成功 reindex 后 Document 的 `active_document_version_id` 原子切换，旧版本保留到新全局快照激活并完成安全回收。

`index_snapshots` 表示整个当前可检索语料库的一次不可变 FAISS/BM25 快照，保存 `id`、`status`、`embedding_signature`、`faiss_path`、`bm25_path`、`manifest_sha256` 和时间字段。`system_state.active_index_snapshot_id` 指向唯一 active snapshot。状态为 `building`、`active`、`superseded`、`failed`。

Snapshot manifest 至少包含 schema version、embedding/analyzer 配置、所含每个 `document_id -> document_version_id` 映射、文档及 chunk 数量、最大 faiss id 和文件 hash。单篇 reindex 创建新的 DocumentVersion，再使用该新版本与其他文档当前版本构建新 IndexSnapshot；不复制其他文档的 Chunk。`active` 前必须完整校验 manifest、数据库映射和所有 DocumentVersion 的 embedding signature。

### 9.6 Session、Message、RetrievalLog

- `sessions`：`id`、`title`、`scope_type`（`all|documents|collection`）、`scope_payload`、时间字段。
- `messages`：`id`、`session_id`、`role`（`user|assistant|system`）、`content`、`citations` JSON、`created_at`。用户消息与最终 assistant 消息永久写 PostgreSQL；流式中断不得保存伪完整回答，可保存 `status=interrupted` 的部分内容。
- `retrieval_logs`：原问题、改写问题、scope、各阶段候选、最终上下文、模型版本、参数快照、答案、各阶段 latency、错误码、时间字段。候选只记录 ID、rank、score 和必要 preview，避免无限膨胀。

## 10. 文档状态机与任务语义

```text
uploaded -> queued -> parsing -> chunking -> embedding -> indexing -> ready
                 \______________________________________________-> failed
ready -> queued (reindex) -> ... -> ready
ready|failed -> deleting -> deleted
```

- API 创建文档和 job 必须在同一数据库事务提交，再投递 ARQ；投递失败时通过 outbox/reconciliation 将 job 恢复到队列，禁止产生永久 `queued` 僵尸记录。
- Worker 必须使用 PostgreSQL advisory lock 或等价机制锁定 `document_id`。
- 每个 job 必须幂等：重复执行不得创建重复 active chunks 或重复 FAISS ids。
- `failed` 保留原文件、最后一个有效 DocumentVersion 和 active IndexSnapshot；首次 ingest 失败时文档不可检索。
- Reindex 使用影子 DocumentVersion 和 IndexSnapshot；两者校验成功后在事务中切换 document pointer 与 system active snapshot，失败不得破坏旧版本。
- 删除采用先标记 `deleting`、从检索 scope 排除，再异步清理。成功后删除数据库业务记录与上传文件；索引通过新快照去除对应 IDs。若清理失败，保持 `deleting` 并可重试。

## 11. Loader 规范

### 11.1 接口

```python
class BaseLoader(Protocol):
    supported_extensions: frozenset[str]

    def load(self, path: Path) -> ParsedDocument: ...
```

Registry 以小写扩展名选择 Loader。未知扩展名返回 `UNSUPPORTED_MEDIA_TYPE`。增加 Loader 只能新增实现和注册，不得修改 ingestion pipeline。

### 11.2 ParsedDocument

```python
class ParsedDocument:
    title: str
    paragraphs: list[Paragraph]
    metadata: dict[str, JsonValue]

class Paragraph:
    type: Literal["text", "markdown", "table", "code"]
    content: str
    page: int | None
    line_start: int | None
    line_end: int | None
    metadata: dict[str, JsonValue]
```

所有 loader 必须：规范换行为 `\n`；移除 NUL；保留段落顺序；不擅自翻译或摘要；在可获得时保留 page；计算统一的 normalized line range。

### 11.3 格式边界

- PDF：用 PyMuPDF 按页提取文本块并按坐标排序；空文本页比例超过 80% 且总字符少于 200 时返回 `OCR_REQUIRED`；不承诺公式和复杂表格结构。
- DOCX：保留段落、Heading 样式、表格和代码风格段落；页码通常为空。
- Markdown：识别 ATX/Setext heading、paragraph、fenced code、表格；保留代码语言 metadata；禁止执行 HTML 或脚本。

## 12. Chunking 规范

### 12.1 默认配置

```yaml
small_document_not_chunk: true
small_document_char_threshold: 2048
max_chunk_chars: 800
sentence_merge_num: 12
sentence_on: true
table_on: true
title_chunk_on: true
need_chapter: false
code_not_add_index: false
retrieval_content_max_chars: 30000
md_heading_max_level: 10
neighbor_window: 1
```

配置必须整体写入 `DocumentVersion.chunk_config`。改变会影响 Chunk 边界的配置必须触发对应文档 reindex。

以上是代码中的规范配置名：原材料的 `max_len` 统一为 `max_chunk_chars`，`page_content_shortened` 统一为 `retrieval_content_max_chars`。Heading tree 始终构建，因此不保留 `title_on` 或 `need_chapter_structure` 开关；是否生成标题 Chunk 只由 `title_chunk_on` 控制；MVP 不实现 `need_chapter_merge`。禁止为了兼容草稿名称而在内部维护多套别名。

### 12.2 通用规则

1. 若 normalized 全文不超过 2048 字符，生成一个 `text` chunk；标题和 section metadata 仍保留。
2. 普通文本先按中英文句末标点和换行确定性分句；最多合并 6 句且不超过 800 字符。
3. 单句超过 800 字符时，优先按分号、逗号和空白继续拆分；仍超长时硬切，并记录 `hard_split=true`。
4. Chunk 顺序必须与原文顺序一致且每次运行结果确定。
5. `retrieval_content` 为文档标题、section path 和 raw content 的确定性拼接；`raw_content` 不添加检索前缀。
6. `sentence_on=false` 时跳过句子合并，以 normalized paragraph 为基本单元，超长段落仍按规则 3 拆分；`table_on=false` 时表格序列化为普通 text 元素；`title_chunk_on=false` 时只保留 section metadata，不生成独立 title chunk。

### 12.3 Markdown pipeline

```text
Element parsing -> Heading tree -> Parent merge -> Fine split
-> Optional chapter chunk -> Retrieval title prefix
```

Heading tree 节点必须保存 title、level、section_path、content、parent、children、character_count。标题跳级时挂到最近较低级祖先；文档开头无标题内容挂到虚拟 root，虚拟 root 不生成 title chunk。

相邻元素仅在 section path 相同且合并后不超过 800 字符时合并。代码块与表格不得同普通段落合并。`title_chunk_on=true` 时非空标题可以生成 `title` chunk。

表格 chunk 必须包含 section context、header 和一组完整 rows；每个拆分片段重复 header。单行过长允许保留超限并标注，不得把无 header 的孤立数据行作为 chunk。

`need_chapter=true` 时生成不默认进入最终 top-k 的 chapter chunk，并以 `chapter_chunk_id` 关联子 chunk。Chapter 用于 rerank 后扩展，禁止与 child 在首轮候选中造成重复优势。

## 13. 索引规范

### 13.1 Dense

- Embedding adapter 输入 `list[str]`，输出二维 float32 numpy array 和 dimension；
- E5 默认：query 使用 `query: `，文档使用 `passage: `；
- 所有向量写入前 L2 normalize；零向量必须拒绝；
- FAISS 使用 `IndexIDMap2(IndexFlatIP(dimension))`；
- `faiss_id` 使用数据库 sequence 分配的非负 int64，并由 PostgreSQL 映射到 chunk id；
- 文件写到临时路径，fsync/校验后原子 rename；禁止原地修改 active 快照。

### 13.2 BM25

实现 Okapi BM25，默认 `k1=1.5`、`b=0.75`，IDF：

```text
log((N - df + 0.5) / (df + 0.5) + 1)
```

Analyzer 必须小写英文、保留数字和连字符术语、使用 jieba 处理中文，并为 `BGE-M3`、`DDPM`、`DDIM`、`FID`、`R-Precision`、`InfoNCE`、`CLIP`、`EEG` 提供用户词典机制。禁止无条件过滤数字。

新增或删除文档后必须依据当前 corpus 的 N、df、doc_len 和 avgdl 重算相关统计；禁止将新旧 IDF 加权平均。快照切换规则与 FAISS 相同。

### 13.3 一致性

一次 active IndexSnapshot 的 FAISS、BM25 和 manifest 必须相互一致；manifest 引用的每个 DocumentVersion、Chunk 和 embedding signature 必须存在且匹配。启动时校验；校验失败时健康检查返回 degraded，检索返回 `INDEX_UNAVAILABLE`，禁止混用不同 snapshot 或不兼容的 DocumentVersion。

## 14. Retrieval pipeline

```text
scope validation
-> query rewrite
-> Dense top 30 + BM25 top 30
-> RRF(k=60) top 30
-> rerank top 8
-> dedup
-> parent/neighbor expansion
-> context packing
```

### 14.1 Scope

请求必须是以下之一：`all`、非空 `document_ids`、一个 `collection_id`。document 与 collection 同时出现返回 `INVALID_SCOPE`。仅 `ready` 文档参与检索；不存在或不可用的显式 ID 返回 404，不静默忽略。

### 14.2 Rewrite

输入最近最多 8 条消息和当前问题，输出：

```json
{"standalone_query":"...","changed":true}
```

改写只解析指代和补充上下文，禁止回答问题或增加未出现的事实。BM25 查询为原问题 tokens 与改写问题 tokens 的并集；Dense 与 Reranker 使用改写问题。无历史、超时、JSON 解析失败时使用原问题并记录 fallback。

### 14.3 Fusion 与 rerank

Dense、BM25 默认各取 30。RRF：`sum(1 / (60 + rank))`，rank 从 1 开始。禁止默认直接相加 BM25 score 与 cosine score。稳定排序键依次为 RRF 降序、最佳来源 rank 升序、chunk id 升序。

RRF top 30 进入 cross-encoder，按 rerank score 降序取 8。超时或模型不可用时允许降级到 RRF top 8，但响应和日志必须包含 `degraded_reasons=["RERANK_UNAVAILABLE"]`。

### 14.4 Dedup 与扩展

先按 chunk id，再按 content hash 去重。扩展只发生在 rerank 后：默认取同文档、同 section 的前后各 1 个 chunk；若 chapter chunk 开启，可按剩余预算选择 chapter。扩展 chunk 不获得新的检索分数，保留 `expanded_from_chunk_id`。

### 14.5 Context packing

- 默认总输入预算 8192 tokens，其中 system/prompt 1200、历史 1200、回答预留 1500，检索上下文使用剩余预算；实际值按 provider context limit 取更小值。
- 先按 rerank 顺序选来源，再将同 section 相邻 chunk 合并；不得超过预算后再粗暴截断整个 prompt。
- 单来源过长时按句边界截断，并标注 `truncated=true`。
- 来源顺序确定后分配 `[Source 1]`，marker 在一次回答中不可变化。

```text
[Source 1]
Document: <title>
Section: <A > B>
Page: <6 or unknown>
Chunk-ID: <uuid>
Content:
<raw content>
```

## 15. Generation 与 Citation

`LLMProvider` 必须提供异步 `generate()` 和 `stream()`，业务代码只依赖统一 message、timeout、usage 和 finish reason 模型。

回答 prompt 必须要求：仅依据 Sources；区分来源事实与推断；证据不足时说明；每个可验证结论就近引用 `[1]`；不得生成不存在的 source 编号。服务端在完成后解析引用并校验：

- 引用编号必须存在；
- 每个编号映射至少一个唯一 `chunk_id`；
- 无效 marker 从结构化 citations 中剔除并记录 `INVALID_CITATION_MARKER`，不得伪造映射；
- HTTP/SSE 返回 answer text 与结构化 `citations[]`。

Generation 失败不得保存成功 assistant message。超时返回稳定错误；客户端断开时取消上游请求并把 message 标记为 interrupted。

## 16. API 契约

### 16.1 通用规则

Base path `/api/v1`。JSON 使用 snake_case。错误格式：

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "Document was not found.",
    "details": {},
    "request_id": "uuid"
  }
}
```

输入校验 422；不存在 404；状态冲突 409；类型不支持 415；过大 413；依赖不可用 503；内部错误 500。错误不得返回堆栈、绝对文件路径、SQL 或密钥。

### 16.2 Health 与 models

- `GET /health/live`：进程存活，200。
- `GET /health/ready`：检查 PostgreSQL、Redis、active IndexSnapshot manifest；ready 返回 200，否则 503 与组件状态。
- `GET /api/v1/models/status`：返回配置的模型 ID、revision、device、loaded、最后错误；不返回 key。

### 16.3 Documents

- `POST /documents`：`multipart/form-data`，字段 `file`、可重复 `collection_ids`；返回 202：

```json
{"document_id":"uuid","job_id":"uuid","status":"queued"}
```

- `GET /documents?cursor=&limit=&status=&collection_id=`：列表。
- `GET /documents/{id}`：metadata、status、active job、chunk count、collections。
- `GET /documents/{id}/chunks?cursor=&limit=&kind=`：只返回 `active_document_version_id` 对应的 chunks。
- `POST /documents/{id}/reindex`：无运行中写任务时返回 202；冲突返回 409。
- `DELETE /documents/{id}`：标记 deleting，返回 202 与 cleanup job；重复调用返回当前 job，具备幂等性。

### 16.4 Jobs

- `GET /jobs/{id}`：返回 kind、status、stage、progress、attempt、error 和时间。
- `POST /jobs/{id}/retry`：仅 failed job；创建新 attempt/job 并返回 202。禁止复用已终止 job 的状态历史。

### 16.5 Collections

- `POST /collections`；
- `GET /collections`；
- `GET /collections/{id}`；
- `PATCH /collections/{id}`；
- `DELETE /collections/{id}`，返回 204，不删除文档；
- `PUT /collections/{id}/documents/{document_id}`，幂等，返回 204；
- `DELETE /collections/{id}/documents/{document_id}`，幂等，返回 204。

### 16.6 Search

`POST /search`：

```json
{
  "query": "string 1..4000",
  "scope": {"type":"collection","collection_id":"uuid"},
  "top_k": 8,
  "minimum_should_match": 1,
  "debug": true
}
```

`scope` 还可为 `{"type":"all"}` 或 `{"type":"documents","document_ids":[...]}`。`top_k` 1..20。`minimum_should_match` 可选、默认 1、范围 1..20，只作用于 BM25：候选至少命中 `min(minimum_should_match, unique_query_term_count)` 个唯一 query terms。该值必须写入 retrieval log。响应包含 `original_query`、`rewritten_query`、`results[]`、`degraded_reasons[]`；`debug=true` 额外包含 bm25、dense、rrf、rerank、expanded 和 timings。搜索不调用回答模型。

### 16.7 Chat 与 SSE

- `POST /sessions` 创建 session 和固定 scope；
- `GET /sessions`、`GET /sessions/{id}`、`GET /sessions/{id}/messages`；
- `DELETE /sessions/{id}` 删除会话及消息，不删除论文；
- `POST /chat`：非流式测试接口，输入 `session_id`、`query`；
- `POST /chat/stream`：同样输入，响应 `text/event-stream`。

SSE 必须按以下事件发送，每条 `data` 都是 JSON：

```text
event: meta       data: {request_id,message_id,rewritten_query}
event: sources    data: {sources:[...]}
event: delta      data: {text:"..."}
event: done       data: {usage,citations,finish_reason,degraded_reasons}
event: error      data: {error:{code,message,request_id}}
```

`meta` 只发一次且先于 `delta`；`sources` 最多一次；`done` 与 `error` 必须二选一且作为末事件。服务端每 15 秒发送 SSE comment heartbeat。响应 header 禁止代理缓冲。

## 17. 文件与安全

- 最大文件 100 MiB，扩展名与 magic bytes/ZIP structure 必须同时验证；
- 上传流式写入 `storage/tmp` 并计算 SHA-256，成功后原子移动；禁止一次性读入内存；
- 路径只由服务端随机名构造，拒绝 `..`、绝对路径和符号链接逃逸；
- DOCX/Markdown 内嵌外部 URL 不自动请求；PDF/Office 宏不执行；
- API key 只允许来自环境变量或 secret file，日志必须脱敏；
- CORS 默认仅允许本地前端 origin；MVP 即使无鉴权也默认监听 `127.0.0.1`，对局域网或公网开放必须显式配置并在 README 警告。

## 18. 配置

环境变量使用 `PAPER_RAG_` 前缀。启动时验证，缺失必需项立即失败。

| 变量 | 默认/要求 |
| --- | --- |
| `PAPER_RAG_ENV` | `development` |
| `PAPER_RAG_DATABASE_URL` | 必需 |
| `PAPER_RAG_REDIS_URL` | 必需 |
| `PAPER_RAG_STORAGE_DIR` | `./storage` |
| `PAPER_RAG_MAX_UPLOAD_BYTES` | `104857600` |
| `PAPER_RAG_EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` |
| `PAPER_RAG_EMBEDDING_REVISION` | 必须固定后写入 `.env`/manifest |
| `PAPER_RAG_RERANK_MODEL` | `BAAI/bge-reranker-base` |
| `PAPER_RAG_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` |
| `PAPER_RAG_LLM_MODEL` | `qwen3:4b-instruct` |
| `PAPER_RAG_LLM_API_KEY` | Ollama 可为占位值；其他服务必需 |
| `PAPER_RAG_LLM_CONTEXT_TOKENS` | `8192` |
| `PAPER_RAG_CUDA_DEVICE` | `cuda:0` |
| `PAPER_RAG_GPU_MAX_CONCURRENCY` | `1` |
| `PAPER_RAG_LOG_LEVEL` | `INFO` |

配置响应或日志不得输出 `*_API_KEY`。应用禁止读取 `~/.local/share/opencode/auth.json`；用户若希望与 OpenCode 共用服务，应显式给两者配置相同 base URL/model，并分别注入凭据。

## 19. 可观测性

- JSON 结构化日志，包含 request_id、job_id、document_id、session_id；
- 记录 parsing、chunking、embedding、index、rewrite、dense、sparse、rerank、generation latency；
- 不记录完整原文、完整 prompt、API key；debug retrieval 内容写 PostgreSQL 时受 preview 长度限制；
- `/health/ready` 必须区分 DB、Redis、index、LLM；LLM 不可用不阻止纯检索，但 chat 返回 503；
- 每次评测保存 git commit、依赖 lock hash、index manifest、模型 revision、参数、seed 和时间。

## 20. 测试规范

### 20.1 单元测试

必须覆盖 Loader Registry、文本规范化、SentenceSplitter 边界、heading 跳级、表格 header 重复、content hash、E5 前缀、向量归一化、BM25 公式、RRF tie-break、scope、dedup、neighbor expansion、token budget、citation validator 和状态机。

### 20.2 集成测试

使用真实 PostgreSQL/Redis 临时实例，覆盖上传到 ready、失败重试、并发 reindex 锁、影子索引切换、删除清理、FAISS/BM25 重载、模型不可用降级和 SSE 生命周期。Embedding/reranker/LLM 默认使用 deterministic fake；另设显式 marker 运行真实模型 smoke test。

### 20.3 E2E

至少有 PDF、DOCX、Markdown 各一份许可清晰的小 fixture，验证上传、集合、问答、来源展开、重建和删除。浏览器测试不得依赖公网。

质量门：`ruff check`、`ruff format --check`、`mypy`、`pytest`、前端 lint/typecheck/test/build 全部通过；新增核心分支必须有测试。覆盖率目标后端语句 80%，chunking/retrieval/context 核心模块 90%。

## 21. Evaluation

数据集至少 50 条，每条：

```json
{
  "id": "eval-001",
  "question": "...",
  "scope": {"type":"documents","document_ids":["..."]},
  "relevant_chunk_ids": ["..."],
  "reference_answer": "...",
  "required_citation_chunk_ids": ["..."]
}
```

实现 Recall@1/3/5/10、MRR、nDCG@K、Citation Precision/Recall、检索和端到端延迟。Answer Accuracy 可通过人工或可配置 judge 计算，默认报告必须标注 judge model，禁止把 LLM judge 当客观真值。

至少运行：Dense；BM25；Dense+BM25+RRF；加 Rerank；Full pipeline。另比较 rewrite on/off 与 expansion on/off。输出原始 JSON 和 Markdown 汇总，固定 seed。

## 22. MVP Definition of Done

只有同时满足以下条件才算完成：

1. 三种 MVP 格式能够进入统一 ParsedDocument；
2. 文档任务异步、可查询、可失败重试；
3. Chunk 可回溯 section、page/line、parent/chapter；
4. Dense 与 BM25 索引可保存、加载、增量重建并通过一致性校验；
5. Hybrid + RRF + reranker + dedup + expansion + packing 完整运行；
6. Collection 和 document scope 行为一致；
7. 非流式和 SSE chat 均工作，引用可定位唯一 chunk；
8. 删除和 reindex 不破坏 active IndexSnapshot；
9. 前端完成上传、状态、集合、聊天、来源展开；
10. 至少 50 条人工标注检索问题和完整消融报告；
11. 所有质量门通过，README 能在全新环境复现启动；
12. 不存在文档中未说明的关键默认值、硬编码模型维度或隐式外部服务。

### 22.1 生产闭环硬门

“独立模块存在”或“使用 fake 的单元测试通过”不得视为对应 Phase 完成。MVP 发布还必须同时通过：

1. 生产 ARQ Worker 不得使用 `_DefaultFakeParser`、`_DefaultFakeChunker` 或 fake indexer；上传文本型 PDF 后，页数、字符数和 Chunk 数必须来自真实 Loader/Chunker，且非空论文 `chunk_count > 0`；
2. active IndexSnapshot 必须同时包含所有当前 `ready` 文档的 active DocumentVersion，FAISS、BM25、manifest 和数据库映射必须一致；连续上传第 N 篇论文不得使前 N-1 篇从索引消失；
3. `POST /api/v1/search` 必须在 `all`、`documents`、`collection` 三种 scope 下完成 Dense + BM25 + RRF，返回可回溯的真实 Chunk；
4. Session、非流式 chat 与 SSE chat API 必须挂载到 `app.main`，并通过真实 HTTP 生命周期测试；LLM 不可用时返回稳定 503，不得伪造成功回答；
5. 前端 Documents、Collections、Chat 必须连接真实后端，不得依赖不存在的 endpoint；
6. 私有 6 文档 / 60 问题 benchmark 必须先解析全部 evidence labels，再生成 predictions 和 metrics；任何 answerable 样本 label 无法解析必须失败；
7. 全新数据库上的真实 PostgreSQL/Redis E2E 必须覆盖“上传 → ready → search → chat/citation → reindex → delete”，并证明失败不破坏旧 active snapshot；
8. README 的状态和 Quick Start 必须与实际生产 wiring 一致；禁止以历史 Phase 声明覆盖当前失败证据。

### 22.2 本机验收标准

本项目在目标 Windows/RTX 2060 主机上的发布验收标准为：

- 基础质量门：Ruff、format、mypy、pytest（含 PostgreSQL/Redis integration）、前端 lint/typecheck/test/build 全部通过；
- 语料门：指定 6 份 PDF 的 SHA-256 与 private benchmark manifest 全部匹配，全部文档真实 `ready`、页数合理且 `chunk_count > 0`；
- 索引门：active manifest 包含 6 个 document/version 映射，FAISS 与 BM25 可从磁盘重载，重启后同一查询 top-k 稳定；
- API 门：health、documents、collections、jobs、search、sessions、chat 与 SSE 均有成功和失败路径验证；
- 评测门：先保存 dev/test baseline；候选质量门为 Recall@10 ≥ 0.85、Citation Precision ≥ 0.95、Citation Recall ≥ 0.85、Unanswerable rejection ≥ 0.80。首次真实 baseline 未达门时不得伪称通过，必须保存结果和错误分类；
- 恢复门：reindex 失败继续使用旧版本检索，delete 后所有 scope 不再返回被删文档，Worker/API 重启后 queued/running job 可恢复或明确失败重试。

## 23. 参考资料

- [multilingual-e5-base model card](https://huggingface.co/intfloat/multilingual-e5-base)
- [BGE reranker model card](https://huggingface.co/BAAI/bge-reranker-base)
- [BGE-M3 model card](https://huggingface.co/BAAI/bge-m3)
- [BGE reranker v2 M3 model card](https://huggingface.co/BAAI/bge-reranker-v2-m3)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [OpenCode providers](https://opencode.ai/docs/providers/)
