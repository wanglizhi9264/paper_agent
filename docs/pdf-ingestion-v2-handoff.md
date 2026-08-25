# PDF Ingestion V2 跨机器交接与验收手册

> 状态：V2-0～V2-2 已验收；V2-3 环境门仍待目标机器执行；V2-4 编码交付已完成，真实 MinerU/私有 A/B 待执行
> 环境说明：2026-08-26 按用户指令仅完成编码，不安装模型环境；所有未执行环境门保持 pending
> 规范来源：[`docs/pdf-ingestion-v2-spec.md`](./pdf-ingestion-v2-spec.md)

本文用于把仓库拉到另一台 Windows/Python 3.12 机器后，无歧义地完成 V2-3 的环境验收，然后把后续阶段交给其他代码代理。本文不放宽主规格的完成门。

## 1. 当前精确状态

| 阶段 | 状态 | 已有产物 | 仍需环境验证 |
| --- | --- | --- | --- |
| V2-0 | 已完成 | 8 个合成 PDF fixtures、baseline CLI、11 hard-cases runner | 私有语料可在目标机器复跑 |
| V2-1 | 已完成 | Canonical Document IR、normalizer、validator、serializer、Markdown | 无 |
| V2-2 | 已完成 | PyMuPDF V2 adapter、fast-path quality routing、legacy bridge | 无 |
| V2-3 | 代码完成/环境门待跑 | Docling 2.121.0 adapter、router、setup CLI、A/B CLI、fake conversion tests、model smoke | Docling 模型下载、revision 固定、真实 fixture smoke、六论文 A/B |
| V2-4 | 编码完成/环境门待跑 | 隔离 adapter、subprocess 安全、fake tests、显式 smoke、A/B 结论字段 | 独立 MinerU 与私有 unresolved cases A/B |
| V2-5 | 编码完成/私有门待跑 | IR-native table parent/row/group、metadata、ORM parent mapping、synthetic hard-case contracts | 私有 10 ingestion hard cases |
| V2-6 | 编码完成/运行门待跑 | migration 0002、ORM、IR manager、PDF worker activation、snapshot atomic switch、recovery contracts | PostgreSQL round-trip 与 production E2E |
| V2-7～V2-8 | 未实施 | 见主规格第 19 节 | 按阶段完成门实施 |

禁止在 V2-3 真实 smoke 和 A/B 失败时宣布 V2-3 验收完成，也禁止为规避失败而直接进入 V2-4。

当前用户已于 2026-08-26 明确将本机任务调整为“只完成编码、不安装环境”。因此 V2-4
代码可以落地并接受 deterministic review，但 V2-3/V2-4 的真实环境门仍是 pending，不能据此
宣称发布验收通过。

### 1.1 V2-4 编码交付

- `app/loaders/mineru_adapter.py` 通过 argv list 调用独立 MinerU CLI，不使用 shell；输入限制在
  `storage/uploads`，输出限制在单次 `storage/tmp/mineru/<document_id>`，超时和退出码映射为稳定错误。
- adapter 接受 MinerU content-list JSON/HTML 或 Markdown table，转换为 Canonical Document IR 后运行 validator。
- `pdf_ab` 已允许显式 MinerU challenger，并在 comparison 中输出
  `improved|equivalent|regressed|pending`，没有 anchors 或真实结果时不会猜测结论。
- 显式 smoke：先把公开 fixture 复制到配置的 uploads 目录，再设置
  `PAPER_RAG_RUN_MINERU_SMOKE=1`、启用并固定隔离 MinerU 版本，运行
  `uv run pytest -m model_smoke tests/model_smoke/pdf_v2/test_mineru_isolated_smoke.py -v`。

### 1.2 V2-5 编码交付

- `chunk_document_ir` 是 parser-agnostic 的 Canonical IR 入口；header/footer/figure 不进入首轮检索。
- 每张表生成一个 `table_parent`（`add_to_index=false`）、每个数据行至少一个 `table_row`；
  多级表头、层级 row header、重复 header group 或过长 row 生成 `table_group`。
- row/group retrieval content 使用 `header path: value`，metadata 包含 element/cell/page/bbox、
  fingerprint、row indices 和 `parent_chunk_index`。
- `RealChunker` 先分配全部 ORM chunk UUID，再校验并解析 parent/chapter index；缺失父项使整个构建失败。
- 9 个表格 hard case 加 `13.61/13.09 min` 的公开 synthetic contract proxies 已编码；
  私有 10-case 完成门未执行，保持 pending。

### 1.3 V2-6 编码交付

- `0002_pdf_ingestion_v2` 仅增加 nullable V2 列、parser signature index 和正 schema CHECK，
  downgrade 仅移除本 migration 对象；integration contract 覆盖 upgrade/downgrade/upgrade。
- PDF worker 已从 legacy Paragraph loader 切到 router → Canonical IR → IR-native chunker；DOCX/Markdown 不变。
- IR 先写 building 并校验 hash/schema/quality，再原子 rename 到 immutable versions 路径；delete 清理版本 artifact。
- corpus snapshot 先完整校验并原子移动目录，随后才在同一 DB transaction 切换 snapshot/version/document pointers。
- finalization 失败恢复旧指针和状态；worker startup 标记 stale building rows failed 并隔离 staged/orphan IR。
- PostgreSQL migration、FAISS integration 和真实 worker E2E 因未安装环境均为 pending。

## 2. V2-3 实现契约

V2-3 已编写的代码责任如下：

- `app/loaders/docling_adapter.py`：Docling 延迟导入、PDF conversion、Docling JSON 到 `DocumentIR` 的纯转换、bbox 坐标系转换、reading order、section path、table grid/cell/provenance、稳定错误码。
- `app/loaders/pdf_router.py`：`pdf_parser=docling` 直接选择 Docling；`auto` 只在 PyMuPDF fast path 未通过质量门时转 Docling；MinerU 不进入 auto chain。
- `app/cli/docling_setup.py`：显式下载模型、解析 Hugging Face revision SHA、输出 `.env` 配置、可选真实 PDF check。revision 未完整解析时必须非零退出。
- `app/cli/pdf_ab.py`：对 PyMuPDF/Docling 输出相同 IR、Markdown、quality 和 comparison artifacts；不写数据库，不激活 DocumentVersion。
- `pyproject.toml` / `uv.lock`：`pdf-layout` optional extra 精确固定 `docling==2.121.0`。
- `.env.example` / `app/core/config.py`：Docling parser、model id、revision、device 和 local artifacts 契约。

V2-3 不得改动生产 ingestion service、worker 激活、数据库 schema 或 index snapshot。这些属于 V2-6。

## 3. 新机器环境准备

PowerShell 执行：

```powershell
git pull --ff-only origin main
uv sync --all-groups --extra pdf-layout
Copy-Item .env.example .env
```

第一次显式准备 Docling 模型：

```powershell
uv run python -m app.cli.docling_setup
```

命令成功时会输出以下四项。将实际值写入本地 `.env`，不得提交：

```env
PAPER_RAG_DOCLING_LAYOUT_MODEL=<actual-model-id>
PAPER_RAG_DOCLING_TABLE_MODEL=<actual-model-id>
PAPER_RAG_DOCLING_LAYOUT_REVISION=<actual-commit-sha>
PAPER_RAG_DOCLING_TABLE_REVISION=<actual-commit-sha>
```

任一 revision 为空、`unknown`、branch 名或 tag 时都不通过生产验收。

## 4. V2-3 验收顺序

必须按顺序执行；前一步失败先修复，不跳过。

### 4.1 确定性测试

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -q
```

标准：全部退出码为 0；普通测试不得触发网络或模型下载；`model_smoke` 默认 skip。

### 4.2 真实 Docling smoke

```powershell
uv run python -m app.cli.docling_setup --skip-download --check tests/fixtures/pdf_v2/simple_table.pdf
$env:PAPER_RAG_RUN_MODEL_SMOKE='1'
uv run pytest -m model_smoke tests/model_smoke/pdf_v2 -v
Remove-Item Env:PAPER_RAG_RUN_MODEL_SMOKE
```

标准：

- 恰好解析 1 页；
- 至少有一个 element；
- 恰好有 1 个 3×3 table；
- header 为 `model / is / fid`；
- table cells 存在 bbox provenance；
- `validate_document_ir` 零 issue；
- 未隐式切换 OCR、parser 或浮动 model revision。

### 4.3 公开 fixture A/B

```powershell
uv run python -m app.cli.pdf_ab `
  --input tests/fixtures/pdf_v2/simple_table.pdf `
  --parsers pymupdf,docling `
  --anchors Model,IS,FID,9.46,3.17 `
  --output eval/runs/pdf-v2/v2-3-public
```

标准：两个 parser 都 `ok=true`，`validator_ok=true`，必须 artifacts 齐全，所有 anchors 命中，Docling table 不得 malformed。`eval/runs/` 是运行产物，不提交 Git。

### 4.4 六篇私有论文 A/B

对每篇 PDF 单独执行，`--pages` 使用 hard-case 证据页，`--anchors` 使用 `eval/hard_cases.py` 定义的证据锚点：

```powershell
uv run python -m app.cli.pdf_ab `
  --input '<absolute-private-pdf-path>' `
  --parsers pymupdf,docling `
  --pages '<comma-separated-physical-pages>' `
  --anchors '<comma-separated-anchors>' `
  --output eval/runs/pdf-v2/<paper-run-id>
```

标准：生成 11 个 hard cases 的 A/B 报告；报告必须保留失败原因，不修改 private benchmark 答案或 evidence quote。V2-3 的目标是提供真实对比和 Docling candidate，不要求本阶段修复 11/11；11/11 属于 V2-7 完成门。

## 5. V2-3 失败处理标准

| 失败 | 必须行为 |
| --- | --- |
| `docling` 未安装 | `PDF_PARSER_UNAVAILABLE`，提示 `uv sync --extra pdf-layout` |
| 无文本层且 OCR 关闭 | `OCR_REQUIRED` |
| OOM | `PDF_PARSER_OOM`，不在 adapter 内无限重试 |
| 解析器失败 | `PDF_PARSE_FAILED`，不向客户端暴露绝对路径或堆栈 |
| table grid 与声明 shape 不一致 | `PDF_TABLE_INVALID` warning，计入 malformed，不伪造 cell |
| model revision 空/浮动 | validator 失败，不允许生产激活 |
| Docling A/B 失败 | 保留 PyMuPDF 结果与错误报告，不改数据库 |

## 6. V2-3 最终完成门

只有以下项目全部成立才能在 `memory.md` 将 V2-3 改为“已验收”：

- `docling==2.121.0` 与 lockfile 一致；
- fake conversion tests、router tests、setup CLI tests、A/B CLI tests 全通过；
- Ruff、format check、mypy、全量 pytest 通过；
- 真实 Docling public fixture smoke 通过；
- 六论文/11 hard cases A/B report 已生成；
- parser/model revisions 均为固定 SHA；
- 依赖版本、license、lockfile 变化、模型体积、CPU/GPU 峰值和 A/B 结论已写入 `memory.md`；
- Git 中没有 `.env`、PDF、private benchmark、model weights 或 `eval/runs` artifacts。

## 7. 后续代理执行规则

V2-3 完成门通过后，下一个代理只实施 V2-4。V2-4 完成后停止并交付，不同时实施 V2-5。V2-5～V2-8 同理。

可直接给下一个代理的提示词：

```text
你正在实施 Paper RAG Assistant PDF Ingestion V2。先完整阅读 AGENTS.md、docs/spec.md、docs/proposal.md、memory.md、docs/pdf-ingestion-v2-spec.md 和 docs/pdf-ingestion-v2-handoff.md，然后检查 git status。

先核验 V2-3 的环境门记录；若 V2-3 仍未通过，只修复并完成 V2-3，不开始 V2-4。只有 V2-3 全部完成门已通过时，本次才实施 docs/pdf-ingestion-v2-spec.md 第 19 节的 V2-4 MinerU Challenger。

本次最多完成 V2-4，不实施 V2-5。必须先契约/测试，再实现；完成 adapter 隔离、subprocess argv/path/timeout 安全、确定性 fake tests、显式 smoke、Docling unresolved cases A/B、文档和 memory 记录。不提交 PDF、private benchmark、模型、.env 或评测产物。未运行的测试不得报告为通过。

完成后按“完成、契约、验证、数据、剩余风险、下一阶段”回复并停止。
```

## 8. V2-4～V2-8 任务与验收索引

| 阶段 | 代码产物 | 硬验收 |
| --- | --- | --- |
| V2-4 | 隔离 MinerU adapter、subprocess contract、fake/smoke、A/B | 报告明确 MinerU 是否改善 Docling unresolved cases；不要求默认启用 |
| V2-5 | table parent/row/group chunk、metadata、parent mapping | 10 个 ingestion hard cases 结构断言全通过 |
| V2-6 | migration `0002`、ORM、IR artifact manager、service/worker activation | shadow build、atomic activation、rollback/recovery 全通过 |
| V2-7 | table retrieval/expansion、citation bbox/cell、structured rewrite | 11/11 hard cases evidence resolvable |
| V2-8 | 六论文 reindex、52 labels、60 predictions、release docs | 主规格第 2.3 节全部成立 |

### 8.1 V2-7 coding handoff (2026-08-26)

The coding portion is implemented: ranked table row/group hits receive bounded parent/adjacent-row
context without moving the citation marker; search deduplicates by chunk ID then content hash;
chat sources expose IR element/cell/physical-page bbox provenance; rewrite uses a Pydantic schema,
the last four Session messages, Session scope, and fail-closed fallback. The deterministic
`eval-048` semantic-slot contract and `python -m app.cli.pdf_v2_gate --evidence <private-json>`
acceptance command are included. The gate accepts only the exact 11-case set and exits nonzero for
missing evidence, bad bindings/pages/table bboxes, or incomplete EEG2IM slots.

Per the operator's coding-only instruction, dependency installation and pytest were not run on this
machine. V2-3 real Docling smoke, six-paper A/B, and V2-7 11/11 private evidence remain **pending**;
therefore this section does not claim the V2-7 completion gate passed.

每个阶段的字段、错误码、文件路径、数据库顺序和测试项以主规格为准；本表不替代主规格。
