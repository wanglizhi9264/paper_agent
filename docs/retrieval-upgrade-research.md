# 论文 Agent / RAG 技术调研与首轮落地

调研日期：2026-09-04。范围：GitHub 官方项目、官方源码和作者技术文档。
这是工程方案比较，不是外部 benchmark 的复现；不以 stars 或“最新”替代质量证据。
本次未引入第三方新依赖、复制外部实现或执行外部仓库脚本。

## 1. 参考案例与采用边界

| 来源 | 已核实的机制 | 对本项目的取舍 |
| --- | --- | --- |
| [FutureHouse PaperQA2](https://github.com/Future-House/paper-qa) | 文献搜索、gather evidence、回答分层；上下文化证据摘要与重评分；支持 Agent 和手动调用 | 借鉴证据整理阶段的独立边界。先保留可审计的原文与真实 Chunk ID，不引入逐候选 LLM 摘要成本，也不自动联网找文献 |
| [LlamaIndex AutoMergingRetriever 源码](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/retrievers/auto_merging_retriever.py) | 按 parent-child 关系聚合候选；达到子节点比例阈值时替换成 parent | 借鉴结构身份，不照搬 parent 替换/分数平均。项目要求 marker 指向真实评分 child，所以只做同表 cell 覆盖选择，parent 仍为排名后上下文 |
| [LangGraph](https://github.com/langchain-ai/langgraph)、[持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence) | 用状态图与 checkpoints 支持有状态流程、恢复和人工介入 | 后续 planner/refinement 的参考。本轮只建立阶段 trace；它不是 checkpoint，也不宣称已实现 durable agent。已有 PostgreSQL/ARQ 不因引入 Agent 概念就被替换 |
| [STORM / Co-STORM](https://github.com/stanford-oval/storm) | 多视角提问、知识收集、提纲、生成分层；支持用户提供文档的 retriever | 适用于后续跨论文综述。当前 Top-10 证据不足时扩大多 Agent 并不能保证解决召回，暂不实施 |
| [LightRAG](https://github.com/HKUDS/LightRAG) | 图结构与文本检索结合；项目提供重现及评测接口 | 暂不引入图谱。表格数字、特定 cell 和原文 citation 是本次目标，实体抽取与图存储会增加另一套一致性和评测负担 |

“采用”的是上述机制启发，不是声称本地 cell_coverage 算法来自 PaperQA/LlamaIndex。
这些链接指向调研时的默认分支，未来可能变化；因未 vendor 或安装它们，暂无运行时版本绑定。

还核读了 [PaperQA tools 源码](https://github.com/Future-House/paper-qa/blob/main/src/paperqa/agents/tools.py)：
`EnvironmentState` 保存证据/工具历史，`GatherEvidence` 与 `GenerateAnswer` 分离，并明确限制
同一 gather 操作的并发。它支持此次“先分清状态和证据边界”的决定，不意味着必须引入其运行时。

[Anthropic 的工程指南](https://www.anthropic.com/engineering/building-effective-agents)
区分预定义 workflow 与由模型控制的 Agent，并强调复杂度与延迟/成本的权衡。
因此当前先选择确定性可评测的证据工作流，而不是没有边界的自主循环。

## 2. 重新审视本项目证据

- 历史 52/52 label resolve 与 11/11 hard case 证明指定锚点可达，不等于所有表格
  单元格都正确，更不等于整体 RAG 发布通过。
- 9 月 2 日记录的 71/103 个 gold 进入 RRF80、33 个进入 Top10，是 micro 计数；
  0.74 的候选 oracle 是另一个按问题聚合的指标，不应混为同一个分母。
- 生产 Dense/BM25/RRF 默认 30 来自已批准 spec，不是无意写错的数字；本轮解除硬编码，
  不依据候选 oracle 自动把 80 升为默认。
- 表格 `_retrieval_text` 已包含 document/section/caption/row/column headers；
  “重复添加 parent/header 就会改善 rerank”没有证据支持，故不做。
- 原问题与 rewritten query 的使用差异是原有契约。不能仅凭代码不一致就判断 rewrite
  丢实体。本轮维持 query 语义，避免与 selection 实验混杂。
- 同表 row/group/raw 竞争是合理假设，不是已证明的主要根因；需要真实诊断统计。

## 3. 本轮实现（Phase 7/11 纵向切片）

1. `Settings` 提供 bounded pool 与 `legacy|cell_coverage`，默认仍 30/30/30 + legacy。
2. `app/retrieval/evidence.py` 提供纯函数选择：先 ID，再 hash；实验模式 hash 带
   document/version/table identity；只压制同表 cell IDs 已被已选证据覆盖的 row/group。
3. 不按“同一行”粗暴合并；不同列组依然可选。raw fallback 无 cell，不推断等价。
4. 服务保持实际 scored child 的 ID/score/citation。增加可回放的 candidate ID map、
   full rerank、selection reasons、snapshot/config 和耗时；不把全文复制到 debug。
5. BGE 缓存 keyed by model/revision/device/dtype/batch/max_tokens；显式 512-token 默认；
   只对 CUDA OOM 做一次较小 batch 重试；错误分数拒绝并降级，不吞程序逻辑异常。
6. `eval.evidence_selection` 调用同一生产 search service，而非另写一套排名逻辑。
   PostgreSQL 只读 repeatable-read、固定 snapshot/labels、固定 dev、禁用 HF 网络，
   不调用 LLM、不读 expected answer/rewrite 作为检索输入。

与旧消融的差异：使用真实 service 去重和 snapshot BM25；因此数字不应直接和使用
scientific analyzer、另一套 dedup 的旧脚本横向比较。三个 variants 必须在本 CLI 同跑。
strict Chunk-ID Recall 保持不变；本轮没有通过重写 gold 制造提升。

## 4. 使用与验收

默认行为不变。实验配置（只作 opt-in，不写入用户 `.env`）：

```dotenv
PAPER_RAG_RETRIEVAL_DENSE_TOP_K=100
PAPER_RAG_RETRIEVAL_BM25_TOP_K=100
PAPER_RAG_RETRIEVAL_RRF_TOP_K=80
PAPER_RAG_RETRIEVAL_SELECTION=cell_coverage
```

真实 dev 对比（要求已有 DB、active snapshot、已下载且固定 revision 的 E5/BGE）：

```powershell
.venv/Scripts/python.exe -m eval.evidence_selection --dataset eval/private_benchmark/dataset.resolved.en.json --output-dir eval/results/evidence-selection-2026-09-04 --allow-local-models
```

CLI 只评估 52 个 answerable 中的 dev 部分，query 为真实 user history + 当前问题；
这不是生产 LLM rewrite 的评测，也不评估拒答、citation quality 或完整 Agent 成功率。
输出使用 exclusive create，避免覆盖旧结果；没有结果不等于通过。细节报告位于 ignored
目录，只将无私有内容的汇总同步到 memory。候选数据缺失、scope/snapshot 不匹配、
模型未固定或 degraded 必须失败；不回退到 fake 生成指标。

首轮验收要求：单测/服务测试、Ruff/format/mypy；真实 dev A/B；再决定是否值得冻结
策略并执行独立 held-out test。不能仅看整体 Recall，需同时看表格、比较类、MRR、
被抑制候选是否真正重复、延迟和错误。

有界补充检索的 dev-only before/after：

```powershell
.venv/Scripts/python.exe -m eval.evidence_refinement --dataset eval/private_benchmark/dataset.resolved.en.json --output-dir eval/results/evidence-refinement-2026-09-04 --allow-local-models --allow-llm-calls
```

它固定比较 single_pass 与 bounded_refinement，只使用 answerable dev，且要求显式授权真实
planner 调用。任何模型、数据库、snapshot 或降级错误都 fail closed；test split 不运行。

## 5. 后续顺序

1. 若 coverage 无收益，停止围绕去重微调；审计真正 ranked-out gold 的模型可见 tokens、
   table identity/caption 质量、跨论文候选分布及 label 覆盖。
2. 已实现默认关闭的两轮有界 refinement 及 dev A/B runner；先恢复 DB/LLM endpoint，
   验证 planner 充分性、Recall/MRR、refinement rate 和延迟。未获得真实收益前不启用默认。
3. 若两轮方案有效，再评估跨论文比较的实体/指标显式分解；各子问题必须共享同一
   检索与 citation 实现，且不得突破当前调用预算。
4. LLM evidence judging 先离线对照，明确 token/cost 上限，LLM 摘要不能替代原文引用。
5. 只有长任务确实需要恢复与人工审批时，再比较 LangGraph checkpoint 与现有 ARQ
   持久化任务的实际成本；不预先搭建空的 multi-agent framework。
