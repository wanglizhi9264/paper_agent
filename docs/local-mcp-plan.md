# 本地 Paper RAG MCP 方案

> 日期：2026-10-17。状态：第一版已实现；SDK STDIO 端到端已验证，Codex 宿主配置加载待用户完成。
> 目标：让本地 Codex 等 MCP 客户端调用现有论文库，获取可追溯证据。
> M1/M2 代码与协议验证已交付；M3 的真实 SDK subprocess 调用已验证，尚未声明当前 Codex 聊天已注册这些工具。M4 综述产物未实现。

## 1. 推荐架构

```text
本地 Codex / 其他支持本地 MCP 的客户端
    │ STDIO，由客户端启动子进程
    ▼
Paper RAG MCP adapter（官方 Python MCP SDK）
    │ HTTP，只连接配置中的 loopback API
    ▼
现有 FastAPI + 新 evidence service
    ├─ PostgreSQL：固定检索上下文、任务事实
    ├─ ARQ worker：检索、embedding、rerank
    └─ 现有 FAISS / BM25 / IR / 原文存储
```

优先 STDIO：不增加 MCP 监听端口，客户端负责进程生命周期。API、PostgreSQL、Redis 和 worker 仍需运行；“本地 MCP”不代表系统变成无服务依赖的单文件程序。

adapter 不加载 embedding/reranker，不读取数据库或 LLM 凭据，不启动项目自己的 PaperAgent。外部客户端负责规划和生成，MCP 返回论文证据，避免“外部 Agent 调用内部 Agent”的重复模型循环。

STDIO 的 stdout 只输出协议数据，日志写 stderr。本机 HTTP client 禁止受系统代理影响，API base URL 由配置提供且限制 loopback，不接受工具参数指定任意 URL。

## 2. 第一版工具

| 名称 | 主要输入 | 输出与行为 |
|---|---|---|
| `list_papers` | scope、title query、cursor、limit≤50 | ready 论文 ID、标题、文件名、页数、下一页游标；不触发生成模型 |
| `search_papers` | query、scope 或 context_id、top_k≤8 | context_id、固定 snapshot、原文片段、文档版本、页码、表格导航、降级原因 |
| `read_chunks` | context_id、最多 4 个 chunk 请求 | 每个 chunk 的原文、section、页码、版本与 provenance；支持明确的字符区间续读 |
| `read_table` | context_id、evidence_id、rows/cell_ids | 原始表格值、适用行列头、cell IDs、bbox；同页解释段落独立返回 |

仅开放读取业务数据的操作。任务/上下文的内部审计持久化不意味着工具可以修改论文。设置准确的 readOnly/destructive/openWorld annotations；实际权限必须在服务端校验，annotations 不能代替权限控制。

不把 `finish`、`load_skill` 当作远程业务工具：它们属于项目内部 Agent 的控制机制。首版也不开放 `ask_agent`、任意 SQL、任意文件路径、Python、Shell、上传、删除和 reindex。

## 3. 工具结果契约

结果使用 MCP `structuredContent` + output schema，并附兼容客户端的 TextContent JSON。统一 envelope：

```text
schema_version
context_id / snapshot_id / expires_at
results[]
  chunk_id / document_id / document_version_id
  document_title / section_path / page_start / page_end
  raw_content / content_hash
  element_id / cell_ids / bboxes
  truncated / next_start_char
degraded_reasons[]
```

- 用 UUID 和实际 provenance 定位证据；不返回用户绝对路径、upload 路径、权重路径或密钥。
- raw_content 是引用证据，retrieval_content 不作为引文返回。
- 不提供跨工具全局稳定的 `[1]` 编号：客户端最终汇总后自行编号，绑定 document/version/chunk/cells。
- 不伪造 DOI、作者、年份或 BibTeX。已存储且核实的元数据才可返回；缺失值保留 null。
- MCP 只限制单次工具结果大小，外部客户端负责自己的完整上下文窗口；不能用项目的 DeepSeek tokenizer 代表所有客户端 tokenizer。

当前实现：单次结构化业务响应上限 64 KiB UTF-8；搜索每片段最多 1600 字符；read_chunks 每片段最多 6000 字符并返回续读位置。字符与字节限制分别校验，不称作 token 限制。表格仍最多 40 cells，数值/单位不截断；结果过大返回明确错误并要求缩小行选择。

## 4. 固定 scope 和版本

第一轮 `search_papers` 在一个事务中固定 scope、snapshot 和 DocumentVersion，创建随机 UUIDv4 的 evidence context；后续搜索/读取必须携带 context_id。模型提交的 UUID 不自动代表有权读取，服务端逐次检查 membership、版本、过期和删除状态。

同 context 不允许改 scope。需要其他论文时显式创建新 context；提供相互矛盾的 scope/context 参数返回错误，不静默扩大范围。

已新增 PostgreSQL `evidence_contexts`、`evidence_operations` 表与 `0006_local_evidence` Alembic migration，包含 ID、scope、snapshot 引用、document/version pins、created_at、expires_at、状态。上下文有效期先取固定 30 分钟，不因重连无限延长；adapter 重启后可凭 context_id 继续读取有效上下文。它不是聊天历史，不保存外部客户端完整 prompt。

有效 context 持有快照保留引用；过期清理必须释放引用，和 snapshot GC 联测。重建索引不切换已有 context 的版本；旧资源不可用时返回 `SOURCE_INVALIDATED` 或 `SNAPSHOT_UNAVAILABLE`，要求重新搜索。删除必须先使涉及文档的 context 失效、释放保留引用，再按既有流程清理；不能因 MCP 保留引用使删除永久卡住。

MCP 连接生命周期不能作为业务 scope 的唯一事实来源；显式 context_id 独立于连接。

## 5. 已有实现入口

```text
app/mcp/server.py                  STDIO 协议入口与工具注册
app/mcp/client.py                  类型化 loopback API client
app/schemas/evidence.py            工具/API 输入输出契约
app/api/evidence.py                独立 evidence API
app/services/evidence.py           固定 scope/version 的共享用例
app/models/evidence.py             context 与只读检索任务事实
app/workers/evidence.py            ARQ 检索执行
migrations/versions/0006_local_evidence.py     上述 schema/约束/清理需要的迁移
tests/unit/test_mcp_protocol.py / test_evidence_*.py
tests/integration/test_evidence_pg.py
examples/mcp/codex-config.toml
```

现有 `CorpusTools` 的表格/IR 读取已下沉到 `evidence_reader.TableReader`，原 Agent 和新 API 都复用，保留原入口与回归测试；MCP 目录和分页原文读取位于 evidence service。复用已有检索算法、模型 adapter、GPU 锁和表格 IR，不复制第二套检索器。

现有 `/api/v1/search` 不包含完整固定 context、原文续读和表格读取 API，因此不能声称仅包装现有 REST 就已完成四个工具。新增 route 只负责 schema/状态校验与 service 调用。

检索中的 CPU/GPU 长操作交给 ARQ worker，沿用 GPU concurrency=1；持久化 operation 行同时充当 outbox，带 lease/fencing 与固定 deadline，和 context migration 同切片交付。API 提交任务并返回 operation ID；adapter 在总期限内轮询结果，再返回一次 MCP tool result。列目录和有界原文读取不经过生成 LLM。客户端取消后停止轮询，并尽力取消尚未执行的任务；已执行结果保留到短期清理，不无限重试。

使用官方 `mcp==2.3.0` Python SDK 并更新 uv.lock。SDK 为 MIT，不包含模型权重；生产用可选 extra，dev 组供协议测试使用，adapter 实测不导入 torch。新增 7 个 SDK/传递依赖本机文件约 12.53 MiB：mcp/mcp-types 为 MIT，cffi 为 MIT-0，cryptography 为 Apache-2.0 OR BSD-3-Clause，opentelemetry-api 为 Apache-2.0，pycparser/sse-starlette 为 BSD-3-Clause；这是本机安装文件量，不是跨平台下载大小。替代为 MCP 进程直接读 DB/加载模型，但会扩大进程权限并重复占用显存，不采用。

## 6. 本机配置

先执行 `uv sync --inexact --all-groups --extra mcp`，再运行 `./scripts/start-agent.ps1 -McpOnly` 启动 backend/worker 并启用 evidence API（不要求 Agent tokenizer/LLM、不启动前端）；将以下内容加到 Codex 的本地配置，重新加载 MCP。也可使用 `examples/mcp/codex-config.toml`：

```toml
[mcp_servers.paper_rag]
command = 'D:\Digital_Life\02_Projects\01_paper_agent\.venv\Scripts\python.exe'
args = ['-m', 'app.mcp.server', '--transport', 'stdio']
cwd = 'D:\Digital_Life\02_Projects\01_paper_agent'
env = { PAPER_RAG_MCP_API_BASE_URL = 'http://127.0.0.1:8000' }
startup_timeout_sec = 15
tool_timeout_sec = 90
```

adapter 的工具总期限为 75 秒（含排队和冷启动），外部客户端 90 秒，HTTP 子请求使用更短 connect/read timeout。长时间排队返回稳定错误，不能把 transport 存活等同于模型/索引就绪。提供轻量启动检查，MCP initialize/list_tools 不下载或加载模型。

首版服务仅本机；不自动修改用户全局 Codex 配置。用户配置完成后，以真实客户端 initialize → list_tools → call_tool 验证接通，不能只凭配置文件存在判断成功。

## 7. Skill、Python 与 LaTeX 的配合

MCP 提供论文证据，宿主 Codex 可以用自己的本地工具运行 Python、写 `.tex`/`.bib` 并编译，具体受该会话权限与已安装工具约束。这些能力不会由只读 RAG MCP 自动提供。

后续可另交付客户端 literature-review skill：先定 scope → 收集证据表 → 按方法/实验归纳 → 分章写作 → 校验引用/元数据 → 编译和检查 PDF。MCP prompts 或 resources 也不会自动成为已安装 skill。当前 4-action PaperAgent 不作为长篇综述的唯一执行循环。

本地调用不等于内容完全不离开电脑：若宿主模型运行于云端，工具返回的论文摘录会进入该模型的上下文。严格离线需求需同时选择本地模型宿主。

## 8. 实施切片与验收

1. **M1：契约与共享证据服务。** 新 schema/migration、固定 scope/context、表格与段落读取；原 Agent 不退化。
2. **M2：worker 与四个 MCP 工具。** 有界检索任务、STDIO adapter、结构化结果、错误/超时/取消、样例配置。
3. **M3：客户端端到端验收。** 真实 Codex 调用本地论文库；有据可答、无据明确缺口；记录冷/热耗时与返回大小。
4. **M4：综述工作流。** 独立 skill 和 `.tex`/`.bib`/PDF 产物验收，作为后续任务。

必须验证：

- 协议 initialize、工具发现、schema 合法性；stdout 无日志污染。
- 搜索/读取 scope 无越界；不同 context 不混用；读不存在/已删除/过期证据明确失败。
- reindex 前后保持版本一致，过期/删除与 snapshot GC 不互相卡住。
- 原文、数值、单位、表头、cell IDs/bbox 不丢失；分次选行不覆盖此前证据。
- 不触发内部生成 LLM，不额外加载一份模型；GPU 并发仍为 1。
- 后端断开、模型冷启动、队列超时、工具取消和重连均有结果；普通测试不下载模型。
- deterministic 测试、PostgreSQL 集成、真实 STDIO subprocess 测试、显式真实模型 smoke 分层记录。

不使用已消耗的 frozen test 选参，不把协议接通或当前用户单题成功当成 RAG 质量门通过。

## 9. 文档边界与参考

现有 Agent runtime spec 的“不接入 MCP 外部工具”描述的是当前实现边界；本方案新增的是对外提供证据的 MCP server，不让内部 Agent 自动调用第三方 MCP。主 spec §25 与 proposal §29 已同步新增阶段和契约。

- [Codex 官方 MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)：本地 STDIO、command/args/cwd 及超时配置。
- [MCP 官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)：官方 Python 实现与许可。
- [MCP Tools 规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)：结构化结果、output schema、显式业务状态 handle。

普通 ChatGPT 网页不会读取本地 Codex 配置；本方案首个验收客户端为本地 Codex。未来接入云端产品时另按其连接方式、权限和数据范围配置，不把本地配置等同于全平台已接通。


## 10. 本机验证与限制

2026-10-17 已使用真实 SDK STDIO subprocess 完成 initialize/discover/call：6 篇目录、8 条实际搜索来源、原文读取、表格 12 个 cell IDs 与 bbox/page；最新服务一次检索 24.00 秒，先前冷环境一次 53.08 秒，不代表 p50/p95 或检索质量门。只读工具不请求生成 LLM。PostgreSQL migration upgrade/downgrade/upgrade、FK 保留/释放、scope 与失效缓存测试已执行。完整结果在 memory.md，未使用冻结 Gold 选参。

新 context 固定 30 分钟；旧 context 在 reindex 后继续使用保留的旧版本，删除使其失效。当前 index 文件的通用物理 GC 仍沿用项目既有实现边界，本次验证 FK 能阻止提前删除快照且在 invalidation/expiry 后释放，不能称作新增了完整文件 GC。

对于列出目录，没有模型冷启动；检索含排队/首次模型加载，75 秒总期限会明确失败。MCP initialize 能成功不代表 backend/模型可用，后端关闭时工具返回稳定错误。Codex 当前聊天是否加载工具，需要在宿主重新加载配置后实际调用确认；未修改全局配置。

MCP 对表格附带的解释段落使用 character-bound reader（每段最多 400 字符），可根据返回 chunk_id 通过 read_chunks 续读。响应的兼容 TextContent 会重复结构化 JSON，协议帧可能大于 64 KiB；64 KiB 是业务 envelope 的 UTF-8 上限，不能等同于宿主 token 窗口。
