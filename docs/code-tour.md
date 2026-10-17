# 代码阅读路线

当前实现是可运行的开发版本，Agent 默认 opt-in。工程测试通过不等于论文答案质量门通过。

1. 先读 [产品规格](spec.md) 与 [Agent 规格](paper-agent-runtime-spec.md)，了解 scope、引用、预算和恢复约束。
2. 网页入口：`frontend/src/entry.ts` 分页面加载；`frontend/src/vue/Workspace.vue` 为 Vue 问答页，`stream.ts` 维护持久化事件流，`markdown.ts` 禁止执行模型输出 HTML。其余管理/review 页面暂保留 React。
3. API：`app/api/agent.py` 处理创建、读取、取消、恢复及 SSE；Pydantic 契约在 `app/schemas/agent.py`。
4. 持久化：`app/services/agent_runs.py` 管理事务、幂等、outbox、lease/fencing、checkpoint 和最终消息原子提交；ORM 在 `app/models/agent.py`，迁移为 `0004`、`0005`。
5. 执行：`app/workers/agent.py` 调用 `app/agent/graph.py`，核心有界循环在 `app/agent/runtime.py`。每个模型和工具调用前保存预算，支持恢复。
6. 上下文：`app/agent/context.py` 使用本地 tokenizer，打包历史与实际来源，并预留输出预算；`app/agent/evidence.py` 合并同表多次选行，保留实际 cell IDs 与 bbox。
7. 工具：`app/agent/contracts.py` 定义白名单与参数；`app/services/agent_trial.py` 实现 scope 固定的搜索、原文及 IR 表格读取。它复用 `app/services/retrieval.py` 的既有检索链路。
8. 技能：`app/agent/skills.py` 校验与按需加载 `agent_skills/*/SKILL.md`；技能是阅读指导，不是可执行脚本，也不授予额外权限。
9. 验证：先看 `tests/unit/test_paper_agent.py`、`test_agent_table.py`、`test_agent_runs.py`，再看 `tests/integration/test_agent_trial.py`。Vue 交互测试在 `frontend/src/vue/Workspace.test.ts`。
10. 本地 MCP：从 `app/mcp/server.py` → `client.py` → `app/api/evidence.py` → `app/services/evidence.py`/`app/workers/evidence.py` 阅读；协议测试 `test_mcp_protocol.py` 会启动真实 STDIO 子进程。配置与使用见 [本地 MCP](local-mcp-plan.md)，切片来源见 [切片说明](chunking-guide.md)。

运行见 README。普通测试使用 deterministic fakes，不会自动下载模型。新 Agent dev/holdout 人工标注与语义引用审核仍待完成。

2026-10-17 后续修复：表格选行合并、同页解释段落的独立引用、Agent 部分回答与数据集/指标歧义处理已接通。用户本次问题的真实 smoke 已得到有效引用的分类准确率答案；旧单轮检索默认未变，仍可能漏掉目标行。单题成功不代表整体答案质量验收通过，新的独立 holdout、语义引用审核与延迟统计仍待完成。
