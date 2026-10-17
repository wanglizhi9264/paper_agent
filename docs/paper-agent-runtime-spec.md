# Paper Agent Runtime 规格

> 版本：0.2.0；日期：2026-10-17；状态：工程链路可运行，论文质量发布门待独立评测。
> 适用：现有 Paper RAG Assistant 的可选本地论文 Agent 模式。
> MUST/必须为验收要求；本文件中的新默认值是候选 Agent 模式的初始值，不更改现有 production 默认。

## 1. 目标、边界与现状

在现有论文解析、混合检索、重排和结构化引用上增加上下文管理、模型驱动工具调用、运行时技能加载及可恢复执行。面向论文精读、方法解释、实验核查、多论文比较和证据不足追问。

本期保持单机、单用户、loopback、Python 3.12/FastAPI/PostgreSQL/Redis/ARQ、React/TypeScript，以及 RTX 2060 6 GB、GPU 重任务并发 1。不增加自主联网、论文下载、Shell、任意 Python 执行、多 Agent、MCP 外部工具接入或自动修改知识库。

以下为 2026-10-16 设计时基线与新增要求；当前实现和验证结果见 `memory.md`：

| 领域 | 已有实现 | 必须新增 |
| --- | --- | --- |
| 上下文 | chat 从 DB 取最近 4 条消息；prompt helper 最多接受 8 条；结构化 rewrite、证据来源 | 生产链路统一 token 计数、按预算历史选择、结构化摘要、证据账本 |
| 工具 | single/refinement/routed 固定服务编排 | 统一 registry、参数/返回 schema、模型 tool-call、服务端策略校验 |
| 状态 | Session/Message/Job/RetrievalLog、索引快照 | Run/Step/Checkpoint/Event、租约、恢复和幂等 |
| 技能 | 版本化 prompt presets | SKILL.md 发现、验证、选择、渐进加载、版本固定 |

已有 `app/context/builder.py` 是独立模块，不能因存在该文件就声称生产 chat 已完成精确预算。现有能力与新增能力分别验收。

主规格 §14.7–14.8 的旧工作流调用上限继续适用于旧模式。新模式单独使用本规格预算；这是明确新增的有界本地模式，不授权突破根 AGENTS.md 的 RAG 不变量。检索默认参数、索引、模型签名与 Gold 不因本设计改变。

## 2. 参考方案与选型

资料核验日期：2026-10-16。以下是官方仓库/规范，不以 stars、README 宣传或第三方榜单作为项目质量证明；本文数值与权限规则是本项目设计，并非上游标准要求。

| 参考 | 已核验的机制 | 本项目采用与取舍 |
| --- | --- | --- |
| [Future-House/paper-qa](https://github.com/Future-House/paper-qa)（Apache-2.0） | 论文搜索、证据收集、答案生成可由 Agent 编排，答案关联文献证据 | 复用分工与证据优先模式；保留自有 E5/FAISS/BM25/RRF/reranker，不引入另一套索引或在线元数据链路 |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)（MIT）、[官方持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence) | checkpointer 管理运行状态，store 管理跨会话数据 | 推荐 LangGraph 作为图执行 adapter；业务事实、工具权限和引用契约留在自有 services/domain |
| [anthropics/skills](https://github.com/anthropics/skills)、[Agent Skills 规范](https://agentskills.io/specification) | SKILL.md 元数据、正文和附属资源，渐进披露 | 兼容格式的受限子集；不执行 scripts；技能许可逐包检查，不假定仓库内所有技能同许可 |

推荐实施时引入 LangGraph 核心与 PostgreSQL checkpoint adapter，不引入 Agent Server、LangSmith 云服务、通用预置 Agent 或 PaperQA 运行时。依赖落地前锁定实际版本、核查 Python 3.12/异步 SQL 兼容、许可证和传递依赖，并记录 wheel/安装体积及冷启动基线；当前文档不虚构版本或体积。替代方案是自研有界状态机，但必须通过相同恢复验收，不得静默替换。

LangGraph 仅作为调度机制，不能成为第二套相互矛盾的业务真相源。§9 规定自有事务日志与框架 checkpoint 的恢复优先级。

已锁定 LangGraph 1.2.15，使用服务自有事务 checkpoint；未额外安装 PostgreSQL saver。
当前本机 DeepSeek V4 使用显式 JSON adapter 和固定 SHA tokenizer；原生协议需要具有
工具 chat template 的 tokenizer 和 supports_tools 配置。非 thinking 编码参考
[官方 deepseek-recipe](https://github.com/deepseek-ai/deepseek-recipe)，不执行远程代码。

## 3. 用户故事与输出

| ID | 请求 | 验收行为 |
| --- | --- | --- |
| PA-01 | “解释这篇论文的方法，继续说它的训练目标” | 保持指代、选中论文、语言与已确认目标；重新读取支撑本轮的证据 |
| PA-02 | “比较 A/B 的实验与局限” | 分论文、数据集、指标、实验设置整理；缺失字段显示未找到；不同条件的数值不得直接排名 |
| PA-03 | “这个结论在表 2 哪一行？” | 通过 chunk/table/cell 定位原始数值、行列头、页码，不用历史答案作证据 |
| PA-04 | “按论文精读模板分析” | 加载匹配技能并显示名称/版本，复用同一检索和引用链路 |
| PA-05 | worker 崩溃、浏览器刷新 | 查询同一 run 并恢复事件；已成功工具结果复用，最终消息不重复 |
| PA-06 | 无证据或预算耗尽 | 给出已验证的部分结果与缺口或明确拒答；不会无界搜索或伪造来源 |

回答包括结构化 claims、支持 source IDs、缺口/不可比条件与普通 Markdown 展示文本。只有最终打包的原始来源可产生 citation marker；摘要、技能说明、历史答案不属于论文证据。

实现中的 claims 是段落级引用关联：cited 表示 marker 已映射到实际打包的 Chunk，
不代表自动证明了该段落的语义；无引用段落标为 gap。语义支持、表格数值与可比性
仍须通过 §12 的人工证据审核，不得把引用结构检查当作答案质量分数。

## 4. 架构与依赖方向

```text
REST/SSE -> AgentRunService -> AgentRuntime protocol -> LangGraph adapter
                   |                 |
             RunRepository     ContextManager / SkillRegistry / ToolExecutor
                   |                 |
             PostgreSQL         existing retrieval / IR / LLM protocols
                   |
             outbox -> ARQ worker -> run execution
```

- API 校验、创建 run、查询、取消和转发事件；Worker 执行整个 Agent run，包括检索 GPU 工作；不得把长任务移回 FastAPI event loop。
- 新增 domain protocols：AgentRuntime、CheckpointStore、TokenCounter、ToolHandler、SkillStore。图节点只调用 service，不读取 API Request。
- 将 chat 的 prompt/source assembly 下沉到共享 ContextManager；旧 chat 与新 run 输出复用 citation validator，防止形成两套来源规则。
- 普通 `/search` 继续保持无 LLM 的检索 API。Agent 的 `search_papers` 必须复用现有 Retrieval Engine，禁止递归调用 `gather_chat_evidence` 而产生嵌套 Agent 循环。
- 所有领域 ID 使用 UUIDv4。框架 thread_id 映射 run_id，Session 是多轮对话边界；不得混用两者。

## 5. 上下文处理契约

### 5.1 分层状态

1. 固定层：系统规则、服务端 scope、输出与引用契约；任何压缩不得删除。
2. 任务层：当前 query、目标、选中论文、比较维度、回答语言、已加载技能版本。
3. 对话层：最近完整消息和结构化 SessionSummary；interrupted assistant 默认不进入历史。
4. 工作层：公开任务计划、已完成步骤、证据缺口、工具结果引用；不保存隐藏推理链。
5. 证据层：本次实际读取的原始 Chunk/table cells 以及来源账本。

首期不自动建立跨 Session 的个人知识/事实记忆。会话摘要用于连续性，不能获得系统规则优先级。

### 5.2 计数与预算

每次模型调用发送前必须计数完整 messages、system/skill、tool schemas、tool-call/result envelope、来源 header、当前 query，预留 completion 和 safety margin：

`input_tokens + reserved_output_tokens + safety_margin <= model.context_limit`

- context limit 与 tokenizer/version 来自 ModelProfile；不能用固定字符比率当作准确 tokenizer。
- 本地模型使用对应 tokenizer/chat template；远端模型使用显式配置且验证过的 TokenCounter。未知模型不猜测编码：Agent 模式拒绝启动并返回 `TOKENIZER_UNAVAILABLE`；旧模式保持既有行为。
- 输入 8192 示例：输出保留 1500，安全余量 512，输入最多 6180。该值是总预算，分项上限不要求同时占满。
- 初始上限：system+工具 schemas 1800、skill 正文/资源 1000、summary 500、近期历史 1200、当前 query 800 tokens；证据使用剩余额度。当前 query 超限返回 422，不静默截断。
- 超预算先减少非必要技能资源，再删除较旧完整对话轮/已归档工具正文，最后按相关性减少来源并在句边界截断。工具调用与对应 tool result 必须成组保留/删除。
- 固定层+当前 query 仍超限返回 `CONTEXT_BUDGET_EXCEEDED`。不得截断权限规则或半段 JSON。
- TokenCounter 误差导致 provider context overflow 时，允许一次减小可选上下文重建；同一 run 中消耗一次模型调用预算并留下降级事件。

### 5.3 会话摘要

history 超过 1200 tokens 时尝试生成结构化摘要：`goal, selected_document_ids, entities, comparison_axes, user_constraints, resolved_points, open_questions, source_message_ids, covered_through_sequence`。

摘要引用消息 ID；新增论文事实必须附 evidence ID，未附时标为待核验。摘要生成最多一次/新用户轮、最多 500 输出 tokens，计入 run 总调用预算；失败采用确定性最近完整轮选择，不阻塞回答。摘要不可扩展 scope、启动工具或确认权限。摘要与覆盖位置在一个事务中更新，CAS 防止并发覆盖；原始 messages 保留。

### 5.4 Evidence ledger 与版本

每项证据至少包含 `evidence_id, run_id, snapshot_id, document_id, document_version_id, chunk_id, content_hash, raw_excerpt, section_path, page/line, element_id?, cell_ids?, bboxes?, retrieved_by_step_id`。检索文本与原文分开；只有 raw excerpt/经过映射的原始表格片段用于引用。

Run 开始固定 eligible document/version 集合和 snapshot，保留引用租约防止后台 GC。后续工具不得混读新版本；文档进入 deleting 时立刻从可用 scope 排除。若引用被删除或快照不可用，停止并返回 `SOURCE_INVALIDATED`/`SNAPSHOT_UNAVAILABLE`，不自动切换证据版本。

最终来源打包生成唯一 `marker -> chunk_id`；同一 chunk 的多个摘录合并为一个来源，cell IDs 保持可追溯。citation validator 检查 marker、scope、version、实际 context 成员。对论文的事实性 claim 必须关联来源；缺证据 claim 标为缺口，不进入有依据结论。最终 validator 是必要条件，不能声称仅凭 ID 存在就证明语义正确。

## 6. 工具调用契约

### 6.1 注册与执行

ToolDefinition 包含 `name, version, description, input_schema, output_schema, read_only, timeout_ms, max_output_tokens, handler_key`。schema 使用 Pydantic 生成的 JSON Schema，拒绝额外字段。Python handler 只能经启动时白名单注册，不能由 skill 文件动态 import。

ToolExecutionContext 由服务端注入：run/session、有效 scope、snapshot、预算、取消信号。模型参数不得包含可覆盖这些字段的授权值。工具执行依次校验：名称/version、JSON/schema、scope 子集、版本有效性、预算、取消、幂等键，再执行和校验输出。

### 6.2 首期工具集

| 工具 | 输入与限制 | 输出 |
| --- | --- | --- |
| `list_papers` | query 可选；limit 1..20；cursor | scope 内元数据/标题及 document IDs；禁止返回完整文件路径 |
| `search_papers` | query 1..800 tokens；document_ids 可选且必须是 scope 子集；top_k 1..8 | 复用 Dense/BM25/RRF/rerank；候选 evidence IDs、原文片段、页/章节和版本 |
| `read_chunks` | chunk_ids 1..4；必须属于 run 固定 eligible versions | 原始内容与 provenance；首次读取亦可按 scope 内版本授权，不能仅以曾搜索命中作权限 |
| `read_table` | evidence_id、row/cell selector；单次最多 40 cells | IR 中行列头、原始 cell value/单位、bbox；无表格结构返回明确不支持 |
| `load_skill` | skill_name | 固定版本正文及资源目录，不直接返回所有资源 |
| `read_skill_resource` | skill_name、relative_path | 已激活技能包允许的文本资源和 digest；不得执行代码 |

跨论文比较、摘要、方法分析通过 skill 与这些原语组合完成，不建立重复检索工具。最终回答由固定 finalize 节点生成，不给模型任意 `finish(success=true)` 的成功权限。

统一 ToolResult：`call_id, status=ok|error, data, evidence_ids, error_code?, retryable, truncated, usage`。候选列表最多 1600 输出 tokens，read 工具最多 2000，按模型 tokenizer 截断并可二次定向读取。工具失败作为结构化 result 回馈模型；scope/注入攻击不得泄露存在于范围外的文档元数据。

### 6.3 模型协议

扩展现有 LLM protocol，新增独立 `generate_action(messages, tools, tool_choice)`，返回完整 assistant message、typed tool calls、usage/finish_reason；旧 generate/stream 保留兼容。

- tool_calls 使用标准 assistant/tool 消息配对与 call ID，不把工具结果伪装成 user 指令。
- action 调用先使用非流式输出；仅完整解析和验证后执行。最终 answer 可流式，不执行未完成的 streaming tool-call 片段。
- ModelProfile 显式 `supports_tools`、`supports_json_schema`、tokenizer 与 provider contract version，并通过显式 smoke 验证，不根据模型名称推断。
- native tool-call 优先；不支持时可显式启用结构化 JSON action adapter，所有 schema/权限/预算检查相同。二者均不支持时返回 `AGENT_CAPABILITY_UNSUPPORTED`，或由用户预先选择旧 RAG 模式；不得悄悄伪称工具执行成功。
- schema 错误最多一次纠正，计入 LLM/action 预算；不支持的 provider 参数在 adapter 层处理，不能无限换格式重试。

## 7. Skill 加载契约

### 7.1 目录与格式

内置 `agent_skills/<name>/SKILL.md` 纳入版本控制；用户包仅从配置的 storage/skills 根目录加载，不能扫描 Codex 私有 skills/credential store。不从上传论文、聊天附件或网络自动发现技能。

```text
agent_skills/paper-comparison/
  SKILL.md
  references/comparison-rules.md
  assets/comparison-template.md
```

```yaml
---
name: paper-comparison
description: 比较多篇论文的方法、实验条件、贡献和局限；适用于明确选定两篇以上论文的比较任务。
license: MIT
metadata:
  paper-agent-version: "1"
allowed-tools: list_papers search_papers read_chunks read_table
---
```

正文规定流程：确认论文与维度 → 按论文取证 → 检查数据集/指标/训练条件 → 为每个比较结论绑定来源 → 输出差异、不可比条件与缺口。技能不能添加来源中的新事实。

遵循 Agent Skills 的 name/description 约束及目录名一致性；使用安全 YAML parser，禁止自定义对象与重复键。实验性 `allowed-tools` 在本项目是请求集合，最终权限为 `server_allowlist ∩ run_policy ∩ skill_requested_tools`，缺省取 run 基础只读集合。它不授予新权限。保留 load_skill/read_skill_resource 控制工具，但同样计入预算。

### 7.2 渐进加载与确定性

- 启动只加载验证通过的名称、description、digest、状态；正文按需加载，引用资源再按需读取。
- 显式 skill 优先；自动匹配先确定性规则/描述目录，再由 action 模型选择。一次 run 最多激活 2 个；目录注入最多 600 tokens，技能/资源合计遵循 §5 的 1000-token 上限。
- 显式指定不存在/不兼容技能返回 `SKILL_NOT_FOUND`/`SKILL_INCOMPATIBLE`；自动匹配无命中使用通用论文 QA，不强行加载。
- 包 digest 覆盖相对路径和所有被支持文件的内容 hash；run 固定 catalog digest 和包版本，运行中不热切换。相同 name 多根冲突必须拒绝或管理员显式选择，禁止依赖遍历顺序覆盖。
- 包单文件不超过 256 KiB、包合计不超过 2 MiB；拒绝绝对路径、`..`、symlink/junction/reparse point、越根资源和远程资源读取。Windows 大小写与路径规范化后再次验证。
- scripts 可以被识别为不支持资源，但绝不执行；不得宣称完整兼容所有 Agent Skills 执行能力。
- 内置包发布前审核；用户自定义包经显式 CLI validate/enable 后才进入目录，运行时无自动安装工具。包更新创建新 digest，旧包保留至引用 run 结束。

### 7.3 内置论文技能

| 技能 | 必需输出 |
| --- | --- |
| `paper-reading` | 问题、核心思路、方法、实验、贡献、局限，各项有来源或缺口 |
| `method-analysis` | 输入/输出、模块、目标函数、假设、训练/推理流程；公式解析不足需标明 |
| `experiment-audit` | 数据集、划分、baseline、指标/单位、表格 cell、消融、可复现信息 |
| `paper-comparison` | 逐论文 evidence matrix、条件差异、可比/不可比项、支持与冲突证据 |

技能不独立存储论文向量、不自行选 embedding 模型、不调用 shell、不改 scope。每个包均有固定输入输出 fixture 和 license。

## 8. 执行状态机与硬预算

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
                  -> interrupted -> queued (显式 resume 或租约恢复)

running 内部：prepare -> select/load skill -> act -> validate tool
             -> execute -> observe -> act ... -> finalize -> validate citations
```

初始 `agent_runtime_enabled=false`；请求明确选择 `mode=agent` 才使用新模式。普通 chat 与既有 workflow 默认不变。

| 预算 | 初始值 | 执行规则 |
| --- | --- | --- |
| 模型调用总数 | 8 | 包括 rewrite、摘要、action、纠错与最终生成；为 finalize 预留 1 次 |
| action 轮数 | 4 | 一个 action 可提出多个 calls，但逐个校验并串行执行 |
| 工具调用总数 | 10 | load/resource/list/read/search 全部计入；失败尝试也计入 |
| search 次数 | 4 | 包含初始搜索；不能通过换技能重置 |
| 累计 token | 32000 | 所有模型输入+输出；每次调用先预留最大输出，未知 usage 用 TokenCounter 保守计费 |
| 运行期限 | 180 秒 | 从首次 running 起累计，重启/恢复不重置 |
| 单次工具 | 30 秒 | 检索/model I/O 受剩余运行期限约束 |
| GPU 并发 | 1 | 复用全局 semaphore；OOM 只减小 batch 重试一次 |

无新增 evidence IDs 且规范化 action/参数重复两次时停止搜索；scope 违规立即拒绝该调用，第二次违规终止 run。达到预算且已有证据时仅可在预留资源内生成部分答案，标注 `stop_reason=budget_exhausted`；无证据返回拒答。关键 provider 失败或 citation 无法校验不伪装 succeeded。

## 9. 持久化、幂等与恢复

### 9.1 数据与迁移

先 Alembic migration 与约束测试，再实现 services：

| 表/扩展 | 主要字段与约束 |
| --- | --- |
| `agent_runs` | id/session_id/user_message_id、mode/status、scope/version/snapshot pins、skill/model/tool manifests、budgets/usage、stop_reason/error_code、revision、lease_owner/expiry/fencing_token、时间；同 Session 最多一个非终态 run |
| `agent_steps` | run_id/sequence/type/status、call_id、tool_name/version、args_hash、result_ref、usage/error；unique(run_id,sequence)、unique(run_id,call_id) |
| `agent_checkpoints` | run_id/revision、schema_version、state_json、last_event_sequence、graph_version；unique(run_id,revision)；禁止 pickle |
| `agent_events` | run_id/sequence/type、payload_json、时间；unique(run_id,sequence) |
| `session_summaries` | session_id/revision、覆盖消息序号、summary_json、model/tokenizer pins |
| `agent_evidence` | §5.4 来源身份、摘录与 step FK；run 内来源键唯一 |
| `messages` 扩展 | 可空 run_id、稳定 session message_sequence；一个 run 最多一条 complete assistant 消息；历史回填稳定排序 |
| `agent_run_outbox` | run_id/dispatch_generation、投递状态；unique(run_id,dispatch_generation) |

状态、非负计数、terminal finished_at、FK、部分唯一索引必须有数据库约束。文档/快照引用须与现有删除/GC 策略协同：删除优先使 run 失效，不能因 checkpoint 永久阻止用户删除。

### 9.2 提交与恢复

- 创建 user message、run、outbox 同一事务；投递失败由 reconciliation 恢复，Redis 不保存唯一事实。
- Worker 用租约+fencing token 领取；所有 step/event/checkpoint 写入验证 token，旧 worker 不得在租约失效后提交。进程重启重新读取已提交 checkpoint，不从聊天文本猜测进度。
- 每个完成的图节点将业务 step/result、预算、事件、下一状态、checkpoint 在同一 PostgreSQL 事务提交。LangGraph saver 必须通过统一 Unit of Work 或自有 CheckpointStore adapter 接入；禁止默认独立连接双写。
- 若上游 saver 无法参与该事务，自有日志为权威，框架 checkpoint 仅可重建缓存；实现前通过崩溃注入验证，不能把“用了 PostgresSaver”当作原子性证明。
- 工具幂等键为 `(run_id, persisted_action_sequence, call_index)`；重放读取已完成结果。不同 action 的相同参数不等于同一个逻辑调用；无进展检测另行处理。
- 外部模型调用无法保证 exactly-once：调用完成但提交前崩溃可重复产生模型费用。调用前持久化 intent 和预算预留；恢复保守计入不确定调用，保留总上限，不声称无重复计费。
- finalize 的完整 assistant message、citations、run terminal status 与 terminal event 在一个事务提交；重复投递不能产生重复回答。
- 回答生成中断的 partial 内容以 attempt 区分；恢复重新生成时发 reset 事件，前端替换旧 attempt，不追加混合文本。只有 validator 通过的完整回答成为 complete。
- snapshot、skill/tool/model、checkpoint schema/graph version 不兼容时 resume 返回 `RESUME_INCOMPATIBLE`，让用户显式新建 run，不能静默用新版本续跑。

### 9.3 取消与生命周期

浏览器 SSE 断开不自动取消后台 run；显式 cancel 将取消标记持久化，工具边界/模型流 chunk 检查。阻塞 GPU 工作不能强行杀线程，完成后不得向已取消 run 提交成功。worker 租约过期进入 interrupted，最多 2 次自动恢复且保持累计预算；超过上限为 failed。

终态 run 的详细工具正文/checkpoint/events 默认保留 30 天，可配置清理；最终 messages/citations 按 Session 保留。清理不可影响进行中 run；过期事件 cursor 返回 410，客户端重新查询 run 与最终消息。禁止 retention 删除被运行中引用的技能包或索引。

## 10. API、SSE 与前端契约

| Endpoint | 契约 |
| --- | --- |
| `POST /api/v1/agent/runs` | `{session_id, query, skill_names?: [], mode:"agent"}`，Idempotency-Key 必需；202 `{run_id,status,events_url}` |
| `GET /api/v1/agent/runs/{id}` | status、公开 plan、usage、steps、selected skills、stop/error、final_message_id |
| `GET /api/v1/agent/runs/{id}/events` | SSE，Last-Event-ID 按持久化 sequence 恢复 |
| `POST /api/v1/agent/runs/{id}/cancel` | 幂等；202；终态返回当前状态，不修改完整消息 |
| `POST /api/v1/agent/runs/{id}/resume` | 仅 interrupted；Idempotency-Key 必需；非法状态 409 |
| `GET /api/v1/agent/skills` | 验证通过且 enabled 的名称、description、digest、支持能力；不暴露路径 |

相同 idempotency key+相同规范化请求复用 run；相同 key+不同请求返回 409。并发 Session 请求返回 `SESSION_RUN_ACTIVE`。Agent feature disabled 返回明确错误，不创建 user message。

SSE 事件：`run.created, run.started, skill.loaded, tool.started, tool.completed, context.compacted, answer.started, answer.delta, answer.reset, answer.completed, run.interrupted, run.failed, run.cancelled, run.completed`。统一 envelope：`schema_version, run_id, sequence, attempt_id?, type, timestamp, payload`。终态事件恰好一条；传输允许重复投递，客户端以 sequence 去重。heartbeat 没有业务 sequence。

对外 payload 不包含隐藏推理、完整 prompt、密钥、绝对文件路径或未经限制的工具正文；证据通过原有受 scope 校验的来源接口查看。tool.started 只展示工具、公开目的和简要参数。

前端由 TanStack Query 管理 run 查询，独立 SSE hook 管理序号/attempt；具有 loading/empty/error/success/interrupted/cancelled 状态。显示论文范围、激活技能、工具进度、预算停止原因、来源 drawer 和 resume/cancel；刷新可恢复同一 run。保留现有浅色 Apple 风格。

OpenAPI/Pydantic 是契约源，生成或契约测试校验 TS types；旧 chat endpoint 的 body/response/event 不被此次 API 破坏。

## 11. 安全、错误与可观测性

论文、历史答案、技能正文与工具结果都是受限内容，不能覆盖 server policy；论文中的“执行命令”“读取凭据”“更换 scope”按正文处理。skill 可指导方法，不可授权权限。首期全部业务工具只读，无需把每次检索变成人工批准。

所有 input/output/event 大小有限制；密钥只通过 provider 配置注入，不进入 graph state。日志记录 run/step/tool ID、hash、耗时、token、降级与错误码，不记录全文、prompt、reasoning 或用户绝对路径。trace 保存工具选择和结果身份，不保存思维链。

稳定错误码至少包含：`AGENT_DISABLED, AGENT_CAPABILITY_UNSUPPORTED, TOKENIZER_UNAVAILABLE, CONTEXT_BUDGET_EXCEEDED, TOOL_NOT_ALLOWED, TOOL_ARGUMENT_INVALID, TOOL_TIMEOUT, TOOL_OUTPUT_INVALID, SCOPE_VIOLATION, SKILL_NOT_FOUND, SKILL_INVALID, SKILL_INCOMPATIBLE, SOURCE_INVALIDATED, SNAPSHOT_UNAVAILABLE, RESUME_INCOMPATIBLE, RUN_BUDGET_EXCEEDED, SESSION_RUN_ACTIVE, LLM_UNAVAILABLE, CITATION_INVALID`。

指标：工具成功/失败、schema 纠错、预算停止、重复 action、无证据拒答、来源覆盖、模型 token/latency、恢复次数、恢复后重复 message 数；必须区分工具成功与答案质量成功。

## 12. 验证与发布门

### 12.1 确定性与集成测试

CI 使用 deterministic fake LLM/tool/tokenizer，不下载模型。每个纵向切片运行最窄相关测试和根 AGENTS.md 的受影响质量门。

| 类别 | 必须覆盖 |
| --- | --- |
| Context | 中英文、长 query、schema/header 计数、summary 失败、历史工具配对、精确边界、无 tokenizer fail closed |
| Tools | 未知工具、多调用 ID、额外字段、scope 注入、越界 chunk/table、恶意 tool output、超时、重复 action、JSON/native 两适配器 |
| Skills | 名称冲突、YAML 注入/重复键、渐进加载、digest 漂移、资源越根、Windows junction、禁 scripts、预算与权限交集 |
| Evidence | 同 chunk 多片段唯一 marker、表格单位/headers、版本固定、删除即时排除、history/summary 不能作 citation |
| Recovery | 创建后未投递、工具 intent 后、外部调用后未 commit、step commit 后、finalize 前后、租约过期双 worker |
| SSE | 正常、失败、取消、断开重连、重复事件、Last-Event-ID、attempt reset、事件过期 |
| Migration | 全新 PostgreSQL、旧库升级与稳定消息序号回填、唯一约束、回滚边界、并发 run |

恢复集成测试必须使用真实 PostgreSQL/Redis/ARQ，并证明不会重复创建完整 assistant 消息、预算不会重置、旧 worker 无法提交、旧 run 不混读新索引。fake 单测不能代替这些门。

### 12.2 论文任务评测

新建许可明确、固定版本的至少 40 题 Agent suite，覆盖精读、方法、实验/表格、多论文、多轮、不可回答；另建至少 20 条 scope/skill/prompt-injection 对抗 fixture 和 10 个恢复故障场景。样本必须包含人工核验 evidence anchors、预期行为/停止条件、必要工具集合或允许轨迹，不要求所有合理回答走唯一工具顺序。

dev 用于参数/skill/prompt 选择；新增未见 holdout 用于一次锁定发布。现有 standard-v2 frozen test 已消耗，不重新包装成 unseen test；不按工具返回反改 Gold。

同 snapshot/model/provider/scope 比较旧 single_pass、有界工作流、新 Agent；保存 token、端到端 latency、任务完成率、严格检索 Recall、Citation P/R、事实支持、表格数值准确、跨论文条件正确率、拒答率。模型评审只作辅助，关键 claim/table 采用人工证据审核。

工程门：scope 泄漏=0、悬空 marker=0、scripts 执行=0、重复 complete 消息=0、预算越界=0，确定性安全/恢复 fixture 全部通过。质量发布仍必须满足主规格 §22.2 的 Recall/Citation/拒答门；新增 Agent 人工任务完成率目标 ≥80%，表格原始数值/单位正确率 ≥95%。这些是待测门槛，不能声称已达到。

默认提升要求：新 holdout 的任务完成率高于 baseline，Citation P/R 不下降，配对置信区间/样本量与方差公开报告；数据不足或收益不确定继续 opt-in。报告模型成本与 p50/p95；额外预算由用户显式启用深读档，首期不自动扩大。

## 13. 纵向实施顺序与完成定义

1. **A1 Context**：TokenCounter + ModelProfile、共享 production ContextManager、账本与来源契约、预算边界测试；先修复精确预算，不改检索排序。
2. **A2 Read-only tools**：protocol/schema/registry、native/JSON adapter、scope/版本校验、一个 search→read→finalize 的真实闭环；不先铺空工具。
3. **A3 Durable run**：先 migration/API/state machine，再 outbox/ARQ/LangGraph adapter、事务 checkpoint、cancel/resume/SSE 与崩溃注入；运行模式仍 opt-in。
4. **A4 Skills**：validate/discover/load/resource、内置 paper-reading，完成证据闭环后逐个加入 method/experiment/comparison；每个技能有自己的固定案例。
5. **A5 Evaluation/UI**：可见轨迹、来源矩阵、真实模型 capability smoke、Windows/RTX 2060 验收、新 holdout、baseline 对照与发布报告。

各阶段依赖按最小闭环实现；A2 的一次执行可先在 service 层集成测试，公共异步 run API 只在 A3 持久化闭环后启用。不得发布不可恢复的伪 run endpoint。

完成必须同时满足：生产 wiring、migration、OpenAPI/TS、真实 scope/citation、正常/边界/失败测试、持久化恢复、相关质量门、README/setup/model smoke、memory 真实记录。仅提交本 spec 不表示功能完成；当前实现状态与实际验证结果以 memory.md 为准；未通过质量门前保持 opt-in。

## 2026-10-17 问答入口与表格核验修订

按用户明确要求，`/` 与 `/agent` 改用 Vue 3 + Vite + TypeScript strict + TanStack Vue Query；文档、合集、健康与私有 Gold review 暂保留 React 页面，独立懒加载。旧聊天可通过 `/legacy-chat` 访问，默认问答改走持久化 Agent。此为分页面迁移，不宣称全站已移除 React。

`search_papers` 对具备已验证 IR 的表格来源返回同版本表格目录：element_id、行列数、最多 20 行的行表头（每行最多 12 tokens），同表只附一次目录；目录不作为数值结论的证据。Agent 在数值问题的搜索结果只有 baseline 行时应读取同表相关行，而不是重复相近搜索。`read_table` 行选择附带列头与覆盖所选行的合并行表头，禁止附带其他无关行的所有行表头；40 cells 上限仍生效。索引、检索排序和现有生产默认策略不变。

修订补充：表格目录含保守 `max_rows_per_read`（按列数计算，合并表头仍可能使实际 cell 数更高）；超出 40 cells 时返回 `TABLE_SELECTION_TOO_LARGE` 的错误 observation，附 max_cells/selected_cells 和缩小选择提示，不返回证据。不得将失败调用当作论文无答案。此修订仅影响 Agent 工具，不改旧 search/chat 检索默认值。

表格补证据契约：read_table 在同版本、同物理页内附带表格前最近 1 个与表格后最近 2 个 paragraph/caption chunk，每段最多 400 tokens，独立 chunk ID/引用，不混入表格来源。定位使用实际表格 chunks 的 index 区间；不读取其他文档/版本，不跨页无界扩展。工具 observation 的导航摘要仍受 2200 tokens 上限。多次读同一个表格 chunk 的不同 cell selections，必须合并已有内容/cell IDs/bbox，禁止以后一轮替换并丢失已读证据。

Agent 部分回答规则（PA-06 的明确化）：若已有来源支持问题的一部分，必须就受支持部分作答并引用，明确未知项与实验范围；对于数据集/指标歧义，使用来源的精确名称，解释限制并追问，不把子集结果泛化为完整数据集或把分类准确率当生成质量。仅完全没有直接支持的事实时使用固定拒答句。该规则只用于 Agent，旧 chat 的严格拒答策略不变，服务端固定拒答归一化与引用验证仍保留。
