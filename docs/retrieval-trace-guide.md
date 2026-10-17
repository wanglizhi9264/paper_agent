# 检索 Trace 学习指南

启动方式沿用 README 的 `scripts/start-agent.ps1`。打开 `http://127.0.0.1:5173/trace`，输入问题并运行；也可在问答页点击“查看完整执行链路”，以 `/trace?run=RUN_UUID` 恢复记录。Vue 页面显式启用详细记录，直接调用创建 API 时 `trace_enabled` 默认关闭。

本页读取已保存的实际执行数据，不会为展示旧运行而重新检索。旧记录无明细、过期、执行中、失败和来源已清理均单独提示。JSON 导出包含查询与论文摘录，应作为自己的研究数据保存。未增加依赖或数据库迁移。

## 用 MDM / FID 理解查询变化

`MDM 的 FID 是多少？` 直接传入 Agent。Agent 可先定位论文，也可调用 `search_papers`；没有固定的独立 query rewrite。模型可以选择 `MDM FID` 或带具体数据集的检索词，但不能预先保证它一定这样做，更不能把假设查询显示成实际查询。

`list_papers` 仅搜索标题，不搜索正文。标题没命中不能证明论文正文没有答案；如果模型此时结束，本页确实没有 BM25/FAISS 阶段。可以输入“MDM 的 FID 是多少？请检索论文正文，核对数据集和实验条件”继续观察。MDM 缩写可能被误解，查看实际搜索词能帮助区分“检索方向选错”与“正确方向仍未找到证据”。这不是已解决 Agent 全部规划问题的声明。

旧 `/legacy-chat` 入口有历史感知 query rewrite，和这个 Agent 入口不同。本页不伪造旧聊天的运行记录。

## 页面对应的真实计算

1. **问题与查询**：原始问题、工具参数，以及本次 Dense/BM25/精排真正使用的查询；列出额外英文查询是否实际执行。每次搜索分别记录，搜索次数不会合成一条看似单轮的流水线。
2. **BM25**：实际分析器分词、k1/b/N/avgdl、命中候选及逐词 tf/df/IDF/贡献。索引文档单位是 chunk；全索引统计不因本次 scope 重算，scope 限制候选。查询重复词按 unique terms 计算，不会因为重复出现就重复加分。分词预览最多 128 个、逐词贡献最多 64 个词项，超出明确标记截断。
3. **FAISS**：实际 embedding manifest 的模型/revision/维度/pooling/归一化/前缀。adapter 添加前缀，UI 展示编码输入。FlatIP 扫描全索引后在 scope 内取 Top-K；这是精确内积检索，不是 ANN。L2 归一化后内积等于余弦。索引与 query 维度必须相容。
4. **RRF**：展示真正参与融合的列表，逐项列出 `1/(60+rank)`，rank 从 1 开始，缺席为 0。双语模式可能使用四路 RRF 或先按最佳名次合并再做两路 RRF；页面读取实际策略。RRF 不直接相加 BM25 和余弦分数。
5. **精排与选择**：实际 rerank query、模型/revision/长度上限、输入候选数、精排前后名次、选择策略/原因和表格扩展 ID。精排读取 retrieval_content；其分数不是正确概率。精排不可用时明确标记降级，并保留候选原分数，不把原分数冒充精排分数。
6. **上下文与引用**：观察到的合并证据数、最终打包来源、精确 tokenizer 请求输入 tokens、预留输出/安全余量、历史消息数、摘要和压缩状态；逐个来源的页码/章节/单元格和引用编号。累计 tokens 包含预算预留，不是 API 账单。来源打包后仍需引用校验，不保证所有检索候选都进入最终引用。
7. **事件时间线**：记录创建、模型调用、工具调用、上下文打包和最终状态；时间从 run 创建算起。包含流式生成的开始/完成，文本 delta 在 UI 省略。

候选正文分列展示 raw_content 与 retrieval_content，各预览前 1200 字符并提示截断。原文来自 run 固定 document/version，删除或清理后不借用新版本替换；每次工具最多加载 200 个候选并标记未加载数量。页码是 PDF 物理页码，章节取决于解析质量，不保证识别完整。

## 耗时与保留

工具耗时包含 GPU 等待、检索/读取与存储；检索 `total` 不含前置 GPU 等待。`scope` 是既有准备总计，细分 snapshot、embedding_model_ready、scope_db；Dense/BM25 的文件加载计入对应阶段。汇总项和子项存在包含关系，不能全部相加。旧记录没有的新细分指标不事后推算。

详细 Trace 沿用 Agent 保留期限（默认 30 天），清理 step/event/checkpoint 时也移除 run.state 中复制的检索诊断与上下文统计；永久最终答案/引用沿用原存储契约。Trace 不包含隐藏思维链、完整 prompt、API key 或模型向量全文；额外诊断不传回模型，不计入工具观察 token 预算。页面显示的是可观察行为，不是模型内部推理。

## 代码阅读入口

- `app/services/retrieval.py`：实际候选与阶段记录。
- `app/retrieval/bm25.py`：现有评分与只读 explain。
- `app/services/agent_runs.py`：事件参数、结果持久化及保留期清理。
- `app/services/agent_trace.py`：只读 trace 组装、固定版本候选查询。
- `app/schemas/trace.py` / `app/api/agent.py`：API 契约与路由。
- `frontend/src/vue/TracePage.vue` / `TraceView.vue`：运行入口与逐阶段浏览。

API：`POST /api/v1/agent/runs` 增加 `"trace_enabled": true`；之后读取 `GET /api/v1/agent/runs/{run_id}/trace`。运行仍走现有 worker、GPU 并发限制和调用预算。没有新增 shell/Python 执行能力。
