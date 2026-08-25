# PDF Ingestion V2 实施规格

> 状态：Approved for implementation
> 版本：1.0.0
> 日期：2026-08-25
> 适用仓库：`wanglizhi9264/paper_agent`
> 上位规范：[`docs/spec.md`](./spec.md)
> 实施提案：[`docs/proposal.md`](./proposal.md)

本文是 PDF Ingestion V2 的规范性实施文件，目标读者是直接生成代码的 AI 代理和代码审查者。除非用户给出新的明确指令，否则实现不得改变本文的数据契约、失败语义、阶段顺序或验收门槛。

冲突优先级遵循根目录 `AGENTS.md`。本文与 `docs/spec.md` 冲突时以 `docs/spec.md` 为准，并在实现前同步修正文档；禁止在代码中静默选择另一种行为。

---

## 1. 背景与问题定义

当前 PDF Loader 使用 PyMuPDF `page.get_text("blocks")`，将页面压平为 `list[Paragraph(type="text")]`。该路径对普通文本可用，但无法稳定表达：

- 双栏或多栏 reading order；
- 表格行、列、多级表头、合并单元格；
- 公式及其编号；
- caption、figure、header、footer；
- element 到物理页和 bbox 的可回溯关系；
- 物理页码与印刷页码的区别。

私有 benchmark 当前有 60 题，其中 52 题可回答。当前 active corpus 中 41/52 可回答题能够解析出至少一个 evidence label，11 题无法完整解析。失败分层如下：

| 问题 | 责任层 |
| --- | --- |
| DDPM `9.46±0.11 / 3.17` | table structure + Unicode normalization |
| EEG2IM 四个指标 | table row reconstruction |
| DDPM Table 2 | table row reconstruction |
| `H / H+L+FiLM` | multi-header + row binding |
| `T / T+F+KD` | multi-header + row binding |
| LMM-Large | multi-header table |
| ACTOR UESTC | multi-header table |
| ACTOR vs Action2Motion | row structure |
| Motion Intent Accuracy/Sensitivity | table structure |
| `13.61 / 13.09 min` | paragraph reconstruction |
| “加上 FiLM 以后是多少” | query rewrite + table retrieval |

前 10 项首先属于 ingestion problem；第 11 项首先属于 conversational retrieval problem。实现不得用 query rewrite 掩盖错误表格结构，也不得用 PDF 正则修复多轮指代。

---

## 2. 目标、非目标与成功定义

### 2.1 目标

V2 必须实现：

1. 项目自有、解析器无关的 Canonical Document IR；
2. PyMuPDF、Docling、MinerU 可替换 adapter 边界；
3. Docling 作为复杂文本型 PDF 的默认 layout parser 候选；
4. PyMuPDF 作为无复杂版面的快速路径和保底解析器；
5. MinerU 作为隔离部署的 challenger/fallback，不进入主应用核心依赖；
6. table-aware、hierarchy-aware chunking；
7. table row/header/value 的稳定检索表达；
8. chunk、citation 到 page、bbox、element/cell 的可追踪映射；
9. parser signature、IR artifact 和 IndexSnapshot 的版本一致性；
10. 11 个 hard cases 全部可解析，随后生成 60 题真实 baseline。

### 2.2 非目标

V2 首版不实现：

- 任意扫描 PDF 的完整 OCR 产品能力；
- 手写识别；
- 化学结构图语义识别；
- 图表曲线数值反演；
- 跨页复杂表格的人工级完美恢复；
- 使用 LLM 猜测缺失单元格；
- 把 Azure Document Intelligence 设为强制依赖；
- 同一 DocumentVersion 内无记录地混合多个解析器输出；
- 自动修改 private benchmark 的 reference answer 或 evidence quote 以通过测试。

### 2.3 成功定义

只有同时满足以下条件，PDF Ingestion V2 才算完成：

- 11/11 hard cases 能映射到正确 evidence element/chunk；
- 52/52 可回答题均有至少一个 resolved evidence label；
- 表格数值与 header/row 绑定准确率在 hard cases 上为 100%；
- citation 的 physical page 正确率为 100%；
- citation 的 bbox 覆盖目标段落或目标表格单元格；
- 普通文本、DOCX、Markdown 的既有 golden tests 无回归；
- 失败 reindex 保持旧 DocumentVersion 和 active IndexSnapshot 可用；
- RTX 2060 6 GB 下无无限重试、无并发 OOM；
- 生成并保存 60 题 dev/test baseline，不把未达到的质量门报告为通过。

---

## 3. 强制架构决定

### 3.1 Canonical IR 是唯一内部解析契约

所有 Loader 必须输出 `DocumentIR`。Docling JSON、MinerU JSON、Markdown 均为输入/派生格式，不是数据库和业务层契约。

```text
PDF bytes
  -> ParserAdapter
  -> DocumentIR
  -> IR validator
  -> Chunker
  -> Chunk ORM
  -> Embedding/BM25
  -> IndexSnapshot
```

禁止：

- Route、Service、Chunker 直接 import Docling/MinerU 类型；
- 在 Chunker 中按 parser id 分支；
- 只保存 Markdown 而丢弃 bbox/cell/provenance；
- 把第三方 parser 的随机字段原样塞入稳定 API。

### 3.2 Markdown 是派生输出

`DocumentIR -> Markdown` 用于调试、LLM context 和人工检查。JSON IR 才是无损、可校验、可重建的 artifact。

### 3.3 解析器职责

| Parser | 角色 | 首版激活策略 |
| --- | --- | --- |
| PyMuPDF | fast path、普通文本、fallback baseline | 无复杂布局时可激活 |
| Docling | layout-aware 默认候选 | 复杂文本型 PDF 的默认 V2 parser |
| MinerU | challenger、由操作员显式触发的 fallback | 默认不自动激活 |

### 3.4 首版禁止逐页混合激活

首版允许为同一 PDF 生成多个完整 `ParseCandidate`，但一个 DocumentVersion 只能激活一个 candidate。禁止把 Docling 第 1 页、MinerU 第 2 页、PyMuPDF 第 3 页拼成一个未记录来源的 IR。

原因：逐页混合会使 heading tree、跨页表格、reading order、parser signature 和 citation provenance 难以验证。逐页混合只能在后续版本中以显式 `CompositeParserManifest` 引入。

---

## 4. 目录与模块

实现必须使用以下目录边界；允许拆分更多私有模块，但不得改变依赖方向。

```text
app/
├── document_ir/
│   ├── __init__.py
│   ├── models.py
│   ├── normalize.py
│   ├── validate.py
│   ├── serialize.py
│   └── markdown.py
├── loaders/
│   ├── base.py
│   ├── registry.py
│   ├── pdf_router.py
│   ├── pymupdf_adapter.py
│   ├── docling_adapter.py
│   ├── mineru_adapter.py
│   ├── docx.py
│   └── markdown.py
├── chunking/
│   ├── pipeline.py
│   ├── text.py
│   ├── table.py
│   └── models.py
├── services/
│   └── ingestion.py
└── workers/
    └── tasks.py

tests/
├── fixtures/pdf_v2/
├── unit/document_ir/
├── unit/loaders/
├── unit/chunking/
├── integration/test_pdf_v2_ingestion.py
└── e2e/test_pdf_v2_hard_cases.py

eval/
└── private_benchmark/  # ignored，不提交
```

依赖方向固定为：

```text
adapters -> DocumentIR models
chunking -> DocumentIR models
services -> parser/chunker protocols
worker -> services
api -> services
```

`document_ir` 不得 import `loaders`、ORM、FastAPI、ARQ、FAISS、LLM。

---

## 5. Canonical Document IR 数据契约

### 5.1 通用规则

- 使用 Pydantic v2 model；
- `extra="forbid"`；
- 所有 ID 使用 UUIDv4；
- bbox 坐标使用 PDF points，原点为页面左上；
- physical page 从 1 开始；
- reading order 从 0 开始且 document 内唯一；
- schema version 首版固定为 `2`；
- 所有文本使用 `\n`，不得含 NUL；
- `raw_text` 与 `normalized_text` 必须分离；
- IR 必须可 JSON round-trip；
- 序列化必须使用稳定 key 顺序，以便计算 SHA-256。

### 5.2 类型定义

实现必须等价于以下契约：

```python
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BoundingBox(IRModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("bbox coordinates are inverted")
        return self


class SourceSpan(IRModel):
    physical_page: int = Field(ge=1)
    printed_page: str | None = None
    bbox: BoundingBox | None = None
    parser_element_id: str | None = None


class ParserManifest(IRModel):
    parser_id: Literal["pymupdf", "docling", "mineru"]
    parser_version: str
    model_ids: dict[str, str] = Field(default_factory=dict)
    model_revisions: dict[str, str] = Field(default_factory=dict)
    options: dict[str, bool | int | float | str]
    signature: str


class TableCell(IRModel):
    id: UUID
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    raw_text: str
    normalized_text: str
    is_column_header: bool = False
    is_row_header: bool = False
    provenance: list[SourceSpan]


class DocumentElement(IRModel):
    id: UUID
    kind: Literal[
        "title", "heading", "paragraph", "list", "table",
        "formula", "figure", "caption", "header", "footer", "code"
    ]
    reading_order: int = Field(ge=0)
    raw_text: str
    normalized_text: str
    section_path: list[str]
    provenance: list[SourceSpan]
    parent_id: UUID | None = None
    table: "TableData | None" = None
    metadata: dict[str, object] = Field(default_factory=dict)


class TableData(IRModel):
    caption: str | None = None
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    header_rows: list[int]
    cells: list[TableCell]
    markdown: str
    html: str | None = None


class PageIR(IRModel):
    physical_page: int = Field(ge=1)
    printed_page: str | None = None
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    element_ids: list[UUID]


class LayoutQualityReport(IRModel):
    replacement_character_count: int = Field(ge=0)
    broken_unicode_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    malformed_table_count: int = Field(ge=0)
    orphan_numeric_ratio: float = Field(ge=0, le=1)
    repeated_header_footer_ratio: float = Field(ge=0, le=1)
    reading_order_confidence: float = Field(ge=0, le=1)
    warnings: list[str]
    hard_failures: list[str]


class DocumentIR(IRModel):
    schema_version: Literal[2] = 2
    document_id: UUID
    title: str
    parser: ParserManifest
    pages: list[PageIR]
    elements: list[DocumentElement]
    quality: LayoutQualityReport
    metadata: dict[str, object] = Field(default_factory=dict)
```

`DocumentElement` 必须用 model validator 强制：`kind="table"` 时 `table` 必填，其他 kind 时 `table` 必须为空。不得使用不唯一的 discriminator union。代码文件必须使用 `from __future__ import annotations` 或在 `DocumentElement.model_rebuild()` 时确保前向引用已解析。

### 5.3 IR 不变量

Validator 必须检查：

1. `PageIR.physical_page` 连续且从 1 开始；
2. `DocumentElement.reading_order` 唯一且连续；
3. 每个 `PageIR.element_ids` 均指向存在的 element；
4. 每个 element 至少有一个 `SourceSpan`；
5. bbox 不越出所属 page，容许浮点误差 `0.5` point；
6. `parent_id` 指向存在的 element 且不能形成环；
7. Table cell `(row, column)` 覆盖范围不越界；
8. 同一 table 不允许两个 cell 覆盖同一逻辑坐标，合并单元格自身跨度除外；
9. table `markdown` 必须由 cells 确定性生成，不接受 parser 提供的未校验 Markdown；
10. `raw_text`、`normalized_text` 不含 NUL；
11. `quality.hard_failures` 非空时 candidate 不得激活；
12. 所有 parser/model revision 必须非空，`unknown` 不可用于生产激活。

---

## 6. Parser Protocol 与候选结果

### 6.1 Protocol

```python
from pathlib import Path
from typing import Protocol
from uuid import UUID


class DocumentParser(Protocol):
    @property
    def manifest(self) -> ParserManifest: ...

    def parse(self, path: Path, *, document_id: UUID) -> DocumentIR: ...


class ParseCandidate(BaseModel):
    parser_id: str
    document_ir: DocumentIR
    artifact_path: str
    artifact_sha256: str
    elapsed_ms: int
```

解析只在 ARQ worker 中执行。FastAPI route 禁止调用 parser。

### 6.2 Parser signature

`ParserManifest.signature` 必须为以下 canonical JSON 的 SHA-256：

```json
{
  "parser_id": "docling",
  "parser_version": "<installed package version>",
  "model_ids": {"layout": "...", "table": "..."},
  "model_revisions": {"layout": "<sha>", "table": "<sha>"},
  "options": {
    "ocr": false,
    "table_structure": true,
    "formula_enrichment": true
  },
  "ir_schema_version": 2,
  "normalizer_version": "unicode-v2"
}
```

JSON 必须：UTF-8、`sort_keys=True`、compact separators、不得包含运行时间和绝对路径。

### 6.3 错误码

必须使用稳定错误码：

| code | 条件 |
| --- | --- |
| `PDF_PARSE_FAILED` | parser 无法完成解析 |
| `PDF_LAYOUT_INVALID` | IR validator 失败 |
| `PDF_TABLE_INVALID` | table cell/row/header 不变量失败 |
| `PDF_UNICODE_CORRUPT` | replacement/broken Unicode 超过硬门 |
| `PDF_READING_ORDER_LOW_CONFIDENCE` | reading order 低于激活阈值 |
| `PDF_PARSER_UNAVAILABLE` | 依赖、模型或隔离服务不可用 |
| `PDF_PARSER_OOM` | GPU OOM，减 batch 一次后仍失败 |
| `OCR_REQUIRED` | 无有效文本层且 OCR 未启用 |
| `IR_ARTIFACT_INVALID` | artifact hash/JSON/schema 不匹配 |

客户端不得收到堆栈、绝对用户路径、模型缓存路径。

---

## 7. 三种 Parser Adapter

### 7.1 PyMuPDF Adapter

PyMuPDF V2 adapter 必须：

- 使用 block/span bbox；
-检测双栏或多栏；
- 去除重复 header/footer，但在 metadata 中记录；
- 对 `page.find_tables()` 的结果构建 TableElement；
- 无法验证表格坐标时记录 `PDF_TABLE_INVALID` warning，不可伪造 cell；
- 普通文本元素按确定 reading order 输出；
- 保留 physical page 和 bbox；
- 不执行 OCR。

Fast path 激活条件必须全部成立：

```text
table_count == 0 OR all tables valid
reading_order_confidence >= 0.95
replacement_character_count == 0
malformed_table_count == 0
orphan_numeric_ratio <= 0.05
hard_failures is empty
```

不满足时路由到 Docling，不允许仅因字符数多而激活 PyMuPDF。

### 7.2 Docling Adapter

Docling adapter 必须：

- 依赖放入新的 optional dependency group `pdf-layout`；
- 固定 package version；
- 固定 Docling 下载模型的 ID/revision；
- 默认 `do_ocr=false`；
- 默认 `do_table_structure=true`；
- 将 DoclingDocument 转换为 Canonical DocumentIR；
- 不把 Docling Markdown 直接写入 Chunk；
- parser 输出必须先通过 IR validator；
- 模型下载必须是显式 setup 命令，测试不得自动下载。

建议配置：

```env
PAPER_RAG_PDF_PARSER=auto
PAPER_RAG_PDF_LAYOUT_PARSER=docling
PAPER_RAG_DOCLING_OCR=false
PAPER_RAG_DOCLING_TABLE_STRUCTURE=true
PAPER_RAG_DOCLING_FORMULA_ENRICHMENT=true
PAPER_RAG_DOCLING_DEVICE=cpu
```

首版默认 Docling 使用 CPU，避免与 E5/reranker 争抢 6 GB GPU。只有真实 benchmark 证明收益且显存门通过后，才允许配置 `cuda:0`。

### 7.3 MinerU Adapter

MinerU 不得直接安装进主 `.venv`。使用以下二选一隔离边界：

1. subprocess CLI；或
2. loopback HTTP adapter。

默认选择 subprocess CLI，配置：

```env
PAPER_RAG_MINERU_ENABLED=false
PAPER_RAG_MINERU_COMMAND=mineru
PAPER_RAG_MINERU_BACKEND=pipeline
PAPER_RAG_MINERU_TIMEOUT_SECONDS=900
```

安全要求：

- 命令参数使用 argv list，不使用 shell string；
- 输入只允许 storage/uploads 内文件；
- 输出只允许本次 job 的 storage/tmp 子目录；
- timeout 后终止子进程并标记失败；
- stdout/stderr 不写入用户全文；
- JSON/Markdown 输出转换为 DocumentIR 后再验证；
- MinerU candidate 默认只参与 A/B，不自动替换 active version。

### 7.4 Auto Router

`PAPER_RAG_PDF_PARSER` 允许：

- `pymupdf`：只运行 PyMuPDF；
- `docling`：只运行 Docling；
- `mineru`：只运行 MinerU，要求显式启用；
- `auto`：先 PyMuPDF quality probe，简单文档接受，否则运行 Docling。

`auto` 首版规则：

```python
candidate = pymupdf.parse(...)
if fast_path_acceptable(candidate.document_ir.quality):
    return candidate
return docling.parse(...)
```

MinerU 不在首版 `auto` 链内。它只通过显式 A/B 命令运行，避免不可预测的模型成本和 GPU 冲突。

---

## 8. Unicode 与文本重建

### 8.1 双文本原则

- `raw_text`：尽量忠实，用于 citation 和 prompt；
- `normalized_text`：用于 embedding、BM25、label resolver；
- 禁止使用 normalized_text 替代引用原文；
- 禁止把 LLM 修复后的内容写为 raw_text。

### 8.2 Normalizer v2

`normalize_for_retrieval` 必须按顺序执行：

1. `\r\n`、`\r` -> `\n`；
2. 删除 NUL；
3. Unicode NFKC；
4. 删除 soft hyphen `U+00AD`；
5. ligature 映射：`ﬁ->fi`、`ﬂ->fl`、`ﬀ->ff`、`ﬃ->ffi`、`ﬄ->ffl`；
6. 只在线末形态满足 `letter-\nletter` 时做 dehyphenation；数字负号不得合并；
7. Unicode minus/dash 统一为 ASCII `-`，但 raw_text 不变；
8. 空白折叠为单空格；
9. 保留 `±`、`%`、希腊字母和数学运算符；
10. 记录 replacement character `U+FFFD`，不得静默删除；
11. 输出不得为空，除非原 element 是 figure。

公式另外生成 `search_aliases`：

```text
ε -> epsilon
θ -> theta
Σ -> sigma
μ -> mu
```

aliases 只追加到 retrieval_content，不改 normalized_text 和 raw_text。

### 8.3 段落重建

paragraph reconstruction 必须基于 bbox、字体和 reading order，不允许仅用 regex 拼所有行。

相邻 span 合并条件：

- 同一 physical page；
- 同一栏；
- baseline/垂直间距在配置阈值内；
- 字体尺寸差异不超过 20%；
- 前一行不是 heading/caption/table cell；
- 后一行不是新列表项。

`13.61 / 13.09 min` hard case 必须通过段落 reconstruction 测试。

---

## 9. Table IR 与表格校验

### 9.1 Header binding

对每个数据 cell，必须能确定：

- 直接 column header；
- 多级 column header path；
- row header path；
- table caption；
- section path。

示例：

```json
{
  "row_headers": ["ACTOR (ours)"],
  "column_headers": ["HumanAct12", "FIDtr"],
  "value": "0.12±0.00"
}
```

无法确定 header path 的 numeric cell 计为 orphan numeric cell。`orphan_numeric_ratio > 0.05` 的 table 不可激活。

### 9.2 确定性 Markdown

Markdown 必须从已验证 cells 生成。合并单元格在每个覆盖位置展开 header 文本，数据 cell 不重复生成值。

禁止把空字符串 header 自动命名为猜测字段；使用稳定占位 `column_0`，同时添加 warning，使 hard-case gate 失败。

### 9.3 Table fingerprint

每个 table 计算：

```text
SHA256(caption + canonical header grid + canonical cell grid)
```

用于 A/B 对比和重复检测，不作为业务 ID。

---

## 10. Table-aware Chunking

### 10.1 Chunk 类型

现有数据库 `Chunk.kind` 保持 `table`，通过 `metadata.chunk_subtype` 区分：

- `table_parent`
- `table_row`
- `table_group`

首版不新增数据库 enum 值，避免无必要 schema 扩张。

### 10.2 Table parent chunk

每个 table 必须生成一个 parent chunk：

```text
Document: <title>
Section: <section path>
Table: <caption or stable table label>
Headers:
<canonical header representation>
```

parent metadata：

```json
{
  "ir_schema_version": 2,
  "element_id": "uuid",
  "chunk_subtype": "table_parent",
  "table_fingerprint": "sha256",
  "physical_pages": [5],
  "bboxes": [{"page": 5, "x0": 0, "y0": 0, "x1": 1, "y1": 1}],
  "cell_ids": []
}
```

### 10.3 Table row chunk

每个非 header 数据行至少生成一个 row chunk。`retrieval_content` 使用显式字段绑定：

```text
Document: Denoising Diffusion Probabilistic Models
Section: 4.2 Reverse process parameterization and training objective ablation
Table: Table 2
Row: ∥ε̃ − εθ∥² (Lsimple)
Inception Score: 9.46 ± 0.11
FID: 3.17
```

`raw_content` 使用确定性 Markdown row，并包含 header，不能只保存数字。

row metadata 必须包含：

```json
{
  "element_id": "table uuid",
  "chunk_subtype": "table_row",
  "parent_chunk_index": 12,
  "row_indices": [4],
  "column_header_paths": [["Inception Score"], ["FID"]],
  "cell_ids": ["..."],
  "physical_pages": [5],
  "bboxes": ["..."]
}
```

### 10.4 Table group chunk

以下条件生成 group chunk：

- 多级表头；
- 一行展开后超过 `max_chunk_chars`；
- 同一模型在多个 dataset 分组下有指标；
- row header 有层级。

group 边界必须按 header path，不按任意字符位置切分。

### 10.5 普通文本 Chunk

普通文本继续使用现有 sentence chunking，但输入从 IR paragraph/heading 来。禁止把 table、formula、caption 先转成 paragraph 再切分。

### 10.6 Parent/child 持久化

ChunkResult 先使用 `parent_chunk_index`。写 ORM 时必须在同一 version 内解析成 `parent_chunk_id`。找不到 parent 时整个 DocumentVersion 构建失败，禁止生成悬空引用。

---

## 11. Retrieval 与上下文扩展

### 11.1 索引内容

以下进入 Dense 和 BM25：

- paragraph chunks；
- table row chunks；
- table group chunks；
- formula chunks；
- title chunks。

table parent 不进入 Dense 或 BM25，`faiss_id` 保持 NULL。它只能由命中的 table row/group 通过 `parent_chunk_id` 扩展进入 context。禁止在 V2 首版引入独立 sparse ID 或让 Dense/BM25 使用不同 ID 空间。

### 11.2 Table expansion

Rerank 后执行：

1. 命中 table row/group；
2. 扩展 parent table；
3. 按 query 中 metric/header 选择最多 2 个相邻相关 row；
4. 去重顺序仍为 chunk id，再 content hash；
5. context 中 parent 在 row 前；
6. source marker 指向实际打包的 row/group chunk，不指向未打包内容。

禁止将整个超大表无条件塞入 context。

### 11.3 Query rewrite

多轮 rewrite 输入：

- 当前 query；
-最近 4 条消息，最多 2 轮；
- Session scope；
- 不提供检索结果，避免循环依赖。

rewrite 输出 schema：

```json
{
  "standalone_query": "string",
  "paper_hints": ["string"],
  "dataset_hints": ["string"],
  "method_hints": ["string"],
  "metric_hints": ["string"]
}
```

必须使用 Pydantic 校验；失败回退原 query 并记录 `REWRITE_FAILED` degraded reason。

“那加上 FiLM 以后是多少？”固定验收输出必须保留：

```text
paper: EEG2IM
dataset: ImageNet-4
method: H+L+FiLM
metrics: IS, FID
```

standalone query 的精确措辞可不同，但四个语义槽位必须全部存在。

---

## 12. Citation 契约

每个 API source 必须扩展为：

```json
{
  "index": 1,
  "chunk_id": "uuid",
  "document_id": "uuid",
  "document_title": "...",
  "section_path": ["..."],
  "page_start": 5,
  "page_end": 5,
  "element_id": "uuid",
  "element_kind": "table",
  "cell_ids": ["uuid"],
  "bboxes": [
    {"physical_page": 5, "x0": 10, "y0": 20, "x1": 200, "y1": 40}
  ],
  "content": "...",
  "truncated": false
}
```

兼容要求：现有字段不得删除。新增字段初期可 nullable，但 V2 table chunk 必须全部提供。

前端后续可以用 bbox 做页内高亮；本任务首版只要求 API 契约和测试，不要求实现 PDF viewer。

---

## 13. Artifact、数据库与迁移

### 13.1 Artifact 路径

每个 candidate 写入：

```text
storage/ir/building/<document_version_id>/<parser_signature>/document_ir.json
storage/ir/building/<document_version_id>/<parser_signature>/document.md
storage/ir/building/<document_version_id>/<parser_signature>/quality.json
```

激活后原子 rename 为：

```text
storage/ir/versions/<document_version_id>/...
```

禁止原地覆盖 active artifact。

### 13.2 DocumentVersion 新字段

创建新 Alembic migration `0002_pdf_ingestion_v2.py`，向 `document_versions` 增加：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `parser_id` | varchar(32) nullable | legacy version 可空；V2 ready 必填 |
| `parser_signature` | varchar(64) nullable | SHA-256；V2 ready 必填 |
| `ir_schema_version` | int nullable | V2 固定 2 |
| `ir_path` | text nullable | storage root 内相对路径 |
| `ir_sha256` | char(64) nullable | canonical JSON hash |
| `parse_quality` | jsonb nullable | LayoutQualityReport |

迁移必须：

- 只增加 nullable 列，兼容已有 V1 数据；
- 添加 parser signature index；
- 添加 `ir_schema_version IS NULL OR ir_schema_version > 0` CHECK；
- 不修改旧 DocumentVersion；
- downgrade 只删除本 migration 新增对象；
- integration test 验证 upgrade/downgrade/upgrade。

### 13.3 激活事务

顺序固定：

1. 创建 building DocumentVersion；
2. 在 storage/tmp 或 storage/ir/building 构建 IR；
3. 校验 IR、hash、quality；
4. 生成 chunks；
5. 构建全语料 shadow FAISS/BM25 snapshot；
6. 校验 snapshot/document version/parser signature；
7. 原子移动 IR 和 index artifacts；
8. 同一数据库事务内：新 version ready、旧 version superseded、document pointer 切换、新 snapshot active、旧 snapshot superseded；
9. commit 后清理 building 临时目录。

任何步骤失败：

- 新 version 标记 failed；
- 新 snapshot 标记 failed 或不落库；
- 旧 document pointer 和 active snapshot 不变；
- 失败 artifact 可保留在 `storage/tmp/failed/<job_id>` 供诊断，默认 7 天后清理；
- 客户端返回稳定错误码。

---

## 14. 配置契约

在 `Settings` 和 `.env.example` 增加：

```env
# PDF Ingestion V2
PAPER_RAG_PDF_IR_SCHEMA_VERSION=2
PAPER_RAG_PDF_PARSER=auto
PAPER_RAG_PDF_LAYOUT_PARSER=docling
PAPER_RAG_PDF_NORMALIZER_VERSION=unicode-v2
PAPER_RAG_PDF_FAST_PATH_MIN_READING_ORDER_CONFIDENCE=0.95
PAPER_RAG_PDF_MAX_ORPHAN_NUMERIC_RATIO=0.05
PAPER_RAG_PDF_MAX_REPLACEMENT_CHARACTERS=0

# Docling
PAPER_RAG_DOCLING_OCR=false
PAPER_RAG_DOCLING_TABLE_STRUCTURE=true
PAPER_RAG_DOCLING_FORMULA_ENRICHMENT=true
PAPER_RAG_DOCLING_DEVICE=cpu

# MinerU challenger
PAPER_RAG_MINERU_ENABLED=false
PAPER_RAG_MINERU_COMMAND=mineru
PAPER_RAG_MINERU_BACKEND=pipeline
PAPER_RAG_MINERU_TIMEOUT_SECONDS=900

# Table retrieval
PAPER_RAG_TABLE_EXPANSION_MAX_ROWS=2
```

配置校验：

- parser 枚举非法时启动失败；
- MinerU parser 被选择但未 enabled 时启动失败；
- 阈值不在 0..1 时启动失败；
- IR schema version 非 2 时启动失败；
- production parser/model revision 为空时 worker 首次执行失败并给出明确错误，不自动使用浮动 main。

---

## 15. 依赖与部署

### 15.1 主项目依赖

新增 `pdf-layout` optional group。V2-3 开始时不得先写版本占位符。代理必须先在独立临时环境中使用 `uv add --optional pdf-layout docling` 解析当时最新的非 prerelease 版本，再用一个公开 fixture 执行 Windows/Python 3.12 CPU smoke。通过后立即将解析出的完整版本号改为精确的 `docling==<已验证版本>` 并在同一提交更新 `pyproject.toml` 和 `uv.lock`。若最新版本失败，按版本由新到旧逐个回退 patch，patch 用尽后再回退 minor，每次失败记入 `memory.md`；禁止在仓库中保留占位符、通配符或未固定版本范围。

必须记录：

- package version；
- license；
- lockfile 体积变化；
- 模型下载体积；
- CPU/GPU 峰值内存；
- 替代方案为 PyMuPDF fast path 和隔离 MinerU。

### 15.2 MinerU 隔离

MinerU 使用独立 venv 或 Docker/WSL。不得让 MinerU 改写主项目 PyTorch、transformers、numpy 版本。

### 15.3 显存策略

RTX 2060 6 GB：

- GPU 重任务 semaphore=1；
- Docling 首版 CPU；
- MinerU pipeline 首选 CPU，GPU 测试单独执行；
- E5 embedding 与 BGE reranker 不与 MinerU 并行；
- OOM 只允许减 batch 一次；
- 仍失败返回 `PDF_PARSER_OOM`。

---

## 16. A/B 命令

新增 CLI：

```bash
uv run python -m app.cli.pdf_ab \
  --input <pdf-path> \
  --parsers pymupdf,docling \
  --pages 5,6,10 \
  --output eval/runs/pdf-v2/<run-id>
```

启用 MinerU：

```bash
uv run python -m app.cli.pdf_ab \
  --input <pdf-path> \
  --parsers pymupdf,docling,mineru \
  --pages 5,6,10 \
  --output eval/runs/pdf-v2/<run-id>
```

输出：

```text
manifest.json
pymupdf/document_ir.json
pymupdf/document.md
pymupdf/quality.json
docling/document_ir.json
docling/document.md
docling/quality.json
comparison.json
comparison.md
```

`comparison.json` 至少包含：

- elapsed_ms；
- peak_rss_mb；
- peak_vram_mb；
- table count；
- valid/invalid table count；
- orphan numeric ratio；
- replacement character count；
- evidence anchor matches；
- page/bbox availability；
- parser/model manifest。

CLI 不写数据库、不激活 DocumentVersion、不修改 benchmark。

---

## 17. 11 个 Hard Cases 的固定验收

private benchmark 不提交 Git。公开测试使用最小、许可清晰的合成 PDF fixture；本机验收额外运行用户私有六文档。

私有验收表：

| ID | 必须恢复的结构 |
| --- | --- |
| eval-001 | DDPM row `Ours (Lsimple)` 绑定 IS `9.46±0.11`、FID `3.17` |
| eval-002 | EEG2IM row/header 绑定 Accuracy/F1 与 IS/FID |
| eval-022 | DDPM Table 2 两个 objective row 各自绑定 FID |
| eval-023 | ImageNet-40 的 `H`、`H+L+FiLM` 绑定 IS/FID |
| eval-024 | ImageNet-4 的 `T`、`T+F+KD` 绑定 Accuracy/F1 |
| eval-025 | LMM-Large 在 HumanAct12/UESTC 两组 header 下绑定 FID/Accuracy |
| eval-027 | ACTOR UESTC Transformer/autoregressive decoder 绑定 FIDtest/Accuracy |
| eval-028 | ACTOR/Action2Motion 在 HumanAct12 下绑定 FIDtr/Accuracy |
| eval-029 | Motion Intent 对应模型列绑定 Accuracy/Sensitivity |
| eval-030 | 两种方法分别绑定 `13.61 min`、`13.09 min` |
| eval-048 | rewrite 保留 paper/dataset/method/metrics 四个语义槽位并命中正确 row |

通过规则：不是“页面中出现这些数字”就算通过；必须由同一 row/header binding 或正确 paragraph relation 支持。

---

## 18. 测试要求

### 18.1 单元测试

必须新增：

- IR JSON round-trip；
- bbox order/bounds；
- reading order 唯一性；
- parent cycle；
- TableCell overlap/span；
- header path binding；
- deterministic table Markdown；
- Unicode NFKC/ligature/soft-hyphen/dehyphenation；
- `±/ε/θ/Σ/˜` 保留与 aliases；
- paragraph reconstruction；
- parser signature stable/change；
- PyMuPDF fast-path routing；
- Docling adapter fake conversion；
- MinerU subprocess argv/timeout/path safety；
- table parent/row/group chunk golden；
- parent_chunk_index -> id mapping；
- table retrieval expansion；
- rewrite schema 和 eval-048 semantic slots。

CI 中 Docling/MinerU 使用 deterministic fixture/fake，不下载真实模型。

### 18.2 集成测试

必须新增：

- migration upgrade/downgrade/upgrade；
- IR artifact 写入、hash、原子激活；
- 两篇文档全局 snapshot 保留；
- reindex 失败旧 version/snapshot 保留；
- table chunks 同时进入要求的 Dense/BM25 路径；
- citation element/cell/bbox 映射；
- delete 清理 IR artifact；
- worker restart 后 building/queued job 可恢复或明确失败。

### 18.3 Model smoke

显式 marker：

```bash
uv run pytest -m model_smoke tests/model_smoke/pdf_v2
```

必须覆盖：

- Docling 对一个公开小 PDF 的真实解析；
- 可选 MinerU isolated smoke；
- RTX 2060 或 CPU profile；
- 不检查私有论文内容。

### 18.4 私有 E2E

本机命令：

```bash
uv run python eval/private_benchmark/validate_dataset.py \
  eval/private_benchmark/dataset.json

uv run python eval/private_benchmark/resolve_chunk_labels.py \
  eval/private_benchmark/dataset.json \
  --output eval/private_benchmark/dataset.resolved.json
```

resolver 任一 answerable sample 无 evidence 时退出非零。禁止自动降低匹配阈值直到通过。

---

## 19. 分阶段实施清单

每阶段独立提交，按顺序实施。后续阶段不得以 stub 代替前置阶段。

### V2-0：Baseline 与 fixtures

修改/新增：

- `eval` 诊断命令；
- 合成 table/multicolumn/unicode PDF fixtures；
- 11 hard cases 本机 baseline report；
- `memory.md` 记录 41/52。

完成门：baseline 可重复，未修改生产路径。

### V2-1：Document IR

修改/新增：

- `app/document_ir/*`；
- protocol；
- serializer/validator/normalizer；
- 全部 unit tests。

完成门：无 parser 依赖的 IR 质量门全部通过。

### V2-2：PyMuPDF V2 Adapter

修改/新增：

- bbox/span/column/table adapter；
- fast-path quality routing；
- legacy Paragraph bridge 仅供对比，生产 V2 不使用。

完成门：普通文本无回归，简单表格 fixture 结构正确。

### V2-3：Docling Adapter

修改/新增：

- optional dependency/lockfile；
- explicit model setup；
- adapter 和 fake conversion tests；
- A/B CLI。

完成门：Docling real smoke 通过，11 cases A/B report 生成。

### V2-4：MinerU Challenger

修改/新增：

- isolated adapter；
- subprocess safety；
- Docling unresolved pages A/B。

完成门：报告明确 MinerU 是否提升，不要求默认启用。

### V2-5：Table-aware Chunking

修改/新增：

- table parent/row/group；
- metadata contract；
- parent mapping；
- golden tests。

完成门：10 个 ingestion hard cases 的结构断言全部通过。

### V2-6：Migration 与生产激活

修改/新增：

- `0002_pdf_ingestion_v2.py`；
- ORM；
- artifact manager；
- ingestion service/worker；
- recovery tests。

完成门：shadow build/atomic activation/rollback 全部通过。

### V2-7：Retrieval、Citation、Rewrite

修改/新增：

- table indexing/expansion；
- source schema；
- rewrite structured output；
- eval-048 test。

完成门：11/11 hard cases evidence resolvable。

### V2-8：全量评测与发布

执行：

- 六论文全量 reindex；
- 52/52 labels freeze；
- 60 predictions；
- dev/test metrics；
- regression report；
- README/architecture/troubleshooting/memory 更新。

完成门：本文件第 2.3 节全部成立。

---

## 20. 代码代理执行规则

交给其他 AI 实现时，必须附带以下指令：

1. 先完整阅读 `AGENTS.md`、`docs/spec.md`、`docs/proposal.md`、`memory.md` 和本文；
2. 严格按 V2-0 至 V2-8 顺序；
3. 一次只实现一个纵向阶段；
4. 每阶段必须包含实现、migration/schema、测试、文档和错误处理；
5. 不创建 `pass`、永远成功 endpoint、隐藏 stub；
6. 不提交 PDF、private benchmark、模型权重、IR 产物、`.env`；
7. 不读取 OpenCode credential store；
8. 真实模型下载只能由显式 setup/model smoke 命令触发；
9. 保留用户现有修改；
10. 每阶段更新 `memory.md` 的真实状态和实际验证命令；
11. 未运行的测试不得报告为通过；
12. 任一 hard gate 未通过时不得声明 PDF Ingestion V2 完成。

每阶段交付格式：

```text
完成：实际实现的能力
契约：schema/migration/API 是否变化
验证：实际运行的命令与结果
数据：是否触碰私有 PDF/benchmark（不得提交）
剩余：下一阶段和已知风险
```

---

## 21. 标准质量门

代码质量门：

```bash
uv sync --all-groups --extra pdf-layout
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
uv run pytest --run-integration
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

真实 PDF 门：

```text
hard case evidence resolution = 11/11
answerable label resolution = 52/52
table header/value binding = 100% on hard cases
citation physical page = 100% on hard cases
citation bbox available = 100% on V2 table chunks
```

最终 retrieval/answer 候选门：

```text
Recall@10 >= 0.85
Citation Precision >= 0.95
Citation Recall >= 0.85
Unanswerable rejection >= 0.80
```

若首次 baseline 未达到候选门，保存结果、错误分类和 parser/index manifest，状态标记为 baseline failed；不得调 frozen test 后伪称通过。

---

## 22. 最终验收清单

- [ ] Canonical Document IR v2 已实现并通过 validator tests；
- [ ] PyMuPDF V2 adapter 已实现；
- [ ] Docling adapter 与真实 smoke 已通过；
- [ ] MinerU challenger 有隔离 A/B 结果；
- [ ] Table parent/row/group chunk 已实现；
- [ ] Unicode normalizer v2 已实现；
- [ ] paragraph reconstruction 已覆盖 `13.61/13.09 min`；
- [ ] migration `0002_pdf_ingestion_v2` 可往返；
- [ ] IR artifact 可校验、原子激活、失败恢复；
- [ ] retrieval 支持 row-first + parent expansion；
- [ ] citation 包含 element/cell/bbox；
- [ ] query rewrite 覆盖 FiLM 多轮问题；
- [ ] 11/11 hard cases 通过；
- [ ] 52/52 answerable labels 可解析；
- [ ] 60 题 predictions/metrics 已保存；
- [ ] 全部质量门实际通过；
- [ ] spec/proposal/README/architecture/troubleshooting/memory 已同步；
- [ ] Git 中不存在 PDF、私有评测、模型、IR/index artifact、`.env`。

---

## 23. 可直接交给代码代理的启动指令

将下面整段原样交给代码代理；首次只执行 V2-0，不得跨阶段：

```text
你正在 Paper RAG Assistant 仓库中实施 PDF Ingestion V2。

开始前按顺序完整阅读：
1. AGENTS.md
2. docs/spec.md
3. docs/proposal.md
4. memory.md
5. docs/pdf-ingestion-v2-spec.md

先检查 git status，保留用户现有修改。本次仅实施 docs/pdf-ingestion-v2-spec.md 第 19 节的 V2-0；
不实施 V2-1 或后续阶段，不改变未被 V2-0 要求的产品行为。

执行要求：
- 先补契约/测试，再写实现；
- 完整实现该阶段的产物和完成门；
- 不得留下 pass、TODO、伪实现或永返成功的 endpoint；
- 不提交 PDF、private benchmark、模型权重、IR/index artifact 或 .env；
- 实际运行 V2-0 的最窄测试和所有受影响质量门；
- 更新 memory.md，只记录已实现能力和实际命令结果；
- 如规格与现有代码冲突，先报告冲突及受影响文件，不得静默改变契约。

最终回复固定包含：
完成、契约变化、验证命令与结果、私有数据处理情况、剩余风险、下一阶段。
```

V2-0 验收后，下一次调用只将指令中两处 `V2-0` 替换为 `V2-1`；以此类推，直到 V2-8。任一阶段完成门未通过时，不得进入下一阶段。
