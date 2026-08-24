# AGENTS.md

本文件约束所有在本仓库工作的代码代理。目标不是尽快堆出聊天页面，而是按规范交付可验证、可恢复、可评测的 Paper RAG Assistant。

## 1. 开始工作前

每次任务开始必须按顺序阅读：

1. `docs/spec.md`：规范性产品与技术契约；
2. `docs/proposal.md`：实施顺序、取舍和风险；
3. `memory.md`：当前进度、已发生的实现决策和待办；
4. 与任务路径最近的其他 `AGENTS.md`（若以后新增）。

冲突优先级：用户当前明确指令 > 更深目录的 `AGENTS.md` > 根目录 `AGENTS.md` > `docs/spec.md` > `docs/proposal.md` > `memory.md`。发现冲突必须指出并更新对应文档，禁止在代码中静默选择另一套行为。

## 2. 当前项目事实

- MVP：单机、单用户、本地优先，无鉴权；默认只监听 loopback。
- 目标 GPU：NVIDIA RTX 2060，按 6 GB 显存设计；GPU 重任务并发为 1。
- 格式：文本型 PDF、DOCX、Markdown；OCR 与复杂论文版面不属于 MVP。
- 后端：Python 3.12、FastAPI、SQLAlchemy、PostgreSQL、Redis、ARQ。
- 检索：FAISS CPU FlatIP + 自研 BM25 + RRF + cross-encoder rerank。
- 前端：React + Vite + TypeScript strict。
- LLM：OpenAI-compatible provider；OpenCode 不是运行时依赖。
- 默认 embedding 为 768 维 E5；任何代码都不得硬编码 768 或 1024，实际维度来自 model/index manifest。

## 3. 实施原则

### 3.1 按纵向切片开发

遵循 `docs/proposal.md` 的 Phase 顺序。一个切片应同时包含最小实现、迁移/API schema、测试、必要文档和错误处理。禁止先创建大量空模块、`pass`、伪实现或永远返回成功的 endpoint。

若用户要求的任务跨阶段，只实现完成该任务所需的最小依赖，并在 `memory.md` 记录跳过项和原因。

### 3.2 先契约后实现

- 数据库变化先明确约束并创建 Alembic migration；禁止只改 ORM。
- API 变化先更新 Pydantic schema、OpenAPI/contract test，再写 service。
- Retrieval 行为变化先增加固定输入输出测试；默认值变化还必须准备 eval before/after。
- 状态机变化先更新 `docs/spec.md`。

### 3.3 依赖方向

```text
api -> services -> domain protocols
worker -> services -> domain protocols
adapters(loaders/models/index/llm) -> protocols
models/db -> no api or worker imports
```

- Route 只做解析、权限/状态校验、调用 service 和序列化；禁止包含检索算法或数据库事务细节。
- Service 管理用例和事务；算法模块必须可无数据库单测。
- Embedding、Reranker、LLM、Loader 和 Index 必须通过 protocol/adapter 解耦。
- 前端类型优先从稳定 OpenAPI schema 生成；若手写，必须有契约测试防漂移。

## 4. 代码约定

### 4.1 Python

- 使用完整类型标注；mypy 配置范围内不得增加无说明的 ignore。
- I/O API 使用 async；CPU/GPU 长任务只在 worker 执行，不阻塞 FastAPI event loop。
- 使用 `pathlib.Path`，禁止拼接不可信路径。
- 使用 timezone-aware UTC datetime。
- 业务 ID 使用 UUIDv4；内部 FAISS ID 使用数据库分配的非负 int64。
- Pydantic schema、ORM model、domain model 分离，禁止把 ORM 实例直接作为跨层契约。
- 捕获具体异常；转换为稳定错误码。禁止裸 `except`、吞异常或向客户端返回堆栈。
- 日志使用结构化字段，禁止 f-string 拼接全文、prompt、token 或绝对用户路径。

### 4.2 TypeScript/React

- 开启 `strict`，禁止新增无理由的 `any` 和非空断言。
- 服务端状态使用 TanStack Query；流式消息状态单独封装，不在多个组件复制 SSE parser。
- 每个异步页面必须实现 loading、empty、error、success；破坏性操作需要确认和进行中禁用。
- UI 遵循浅色 Apple 风格：白/浅灰、近黑文本、少量系统蓝；绿色不作为主色或 glow；避免超大标题、重装饰卡片和无意义渐变。
- 来源 marker、source drawer 与后端结构化 citation 绑定，禁止用正则猜测文档来源。

### 4.3 SQL 与存储

- 所有 schema 变化必须有可执行 migration 和约束测试。
- 不使用 Redis 作为永久消息或任务事实来源。
- active FAISS/BM25 文件禁止原地覆盖；只可临时构建、校验、原子 rename/activation。
- 文件系统产物必须在配置的 storage 根目录内；模型权重、uploads、indexes、`.env` 不得提交 Git。

## 5. RAG 不变量

以下为合并前必须人工复核的不变量：

1. `raw_content` 用于引用和 prompt；`retrieval_content` 用于 embedding/BM25，二者不可混用。
2. 字符阈值与 token budget 分开计算。
3. E5 query/passages 前缀必须由 adapter 统一添加。
4. 向量入 FAISS 前必须 L2 normalize；零向量必须失败。
5. embedding 模型、revision、dimension、pooling、normalize 或 prefix 改变必须产生新 model signature，为全部文档创建兼容 DocumentVersion，并重建 IndexSnapshot。
6. active FAISS、BM25、manifest 及其引用的 DocumentVersion 必须相容；单篇 reindex 不复制其他文档的 Chunk。
7. RRF rank 从 1 开始；默认禁止直接相加 BM25 和 cosine score。
8. Rerank 在 fusion 后，neighbor/chapter expansion 在 rerank 后。
9. 去重顺序为 chunk id，再 content hash。
10. Collection/document scope 必须在 Dense 和 BM25 两路都生效，禁止先全局 top-k 后在结果尾部过滤。
11. Citation marker 必须映射到实际打包 source 和唯一 chunk id；禁止伪造或悬空。
12. Reindex 失败保留旧 DocumentVersion 和 active IndexSnapshot；删除先从 scope 排除，再清理所有存储。

## 6. 模型与显存规则

- 默认模型 ID、revision、device、dtype、batch、max tokens 全部来自配置/manifest。
- CI 不下载真实模型，使用 deterministic fakes；真实模型测试使用显式 `model_smoke` marker。
- RTX 2060 上 embedding 与 rerank 使用 FP16；Generator 推荐 Q4。不得假设所有开发机都有 CUDA。
- GPU semaphore 默认 1。遇到 OOM 只允许减小 batch 重试一次；仍失败要返回明确错误并记录，不得无限重试。
- 代码不能读取 OpenCode credential store。若复用服务，只通过显式 base URL/model/API key 配置。
- 不自动下载模型作为普通单元测试副作用；模型准备应是显式 setup/smoke 命令。

## 7. 测试与验证

每项改动至少运行最窄相关测试，并在交付前运行所有受影响质量门。项目脚手架建立后，标准命令预期为：

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
npm --prefix frontend ci
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

若实际脚手架采用不同命令，必须同步更新 README、本文件和 CI。禁止报告未实际运行的测试。无法运行时说明原因、已完成的替代检查和剩余风险。

必须优先测试：

- 状态迁移、重试、幂等和并发锁；
- parser/chunking golden outputs；
- BM25/RRF 手算样例和稳定 tie-break；
- rebuild 与增量索引等价；
- scope 无数据越界；
- SSE 正常、错误、断开三种生命周期；
- 引用映射和 token budget；
- reindex/delete 失败恢复。

## 8. 数据与安全

- 测试 fixture 必须小且许可清晰；禁止提交用户真实论文、聊天数据或评测秘密。
- 上传文件须同时校验扩展名和内容结构，最大 100 MiB，流式写入。
- 默认 bind `127.0.0.1`；扩大监听范围属于安全相关变更，必须更新文档。
- 不在日志、异常、snapshot manifest、前端 bundle 中输出 API key。
- 不执行 Markdown HTML、Office 宏或文档中外部链接。
- 任何删除实现先写覆盖 DB、FAISS、BM25、upload file 的集成测试。

## 9. 文档和 memory 维护

完成一个有意义的实现任务后更新 `memory.md`：

- “当前状态”只写已经存在并验证的能力；
- 在“决策日志”追加日期、决定、原因、影响文件；
- 在“验证记录”写实际命令与结果；
- 删除已完成待办，添加真实剩余项；
- 不记录 API key、完整 prompt、用户论文内容或瞬时调试噪声。

产品行为、数据契约、默认值改变时更新 `docs/spec.md`；里程碑顺序或技术取舍改变时更新 `docs/proposal.md`。仅改 `memory.md` 不能授权偏离 spec。

## 10. Git 与改动边界

- 保留用户已有修改；开始前检查 status，禁止 reset、checkout 或覆盖无关文件。
- 提交只包含当前任务需要的文件；不要顺手格式化整个仓库。
- 依赖新增必须说明用途、许可、运行时体积和替代方案；更新 lockfile。
- 禁止提交：`.env`、credentials、模型权重、uploads、index snapshots、数据库 dump、大型评测输出。
- migrations、API schema、实现和测试应在同一变更中保持可运行。

## 11. 完成定义

代码存在不等于完成。一个任务只有在以下条件同时满足时才可标记完成：

- 实际行为符合 spec 和明确验收条件；
- 正常路径、关键边界和失败路径有测试；
- 相关质量门实际通过；
- migration/config/docs/API 示例已同步；
- 未留下隐藏 stub、硬编码模型维度、吞错或数据一致性缺口；
- `memory.md` 已记录真实状态与验证结果。
