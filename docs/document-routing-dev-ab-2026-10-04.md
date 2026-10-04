# Document Evidence Routing — Standard v2 Dev A/B

日期：2026-10-04

## 范围与不变量

- 数据：standard v2 中 37 道 answerable dev；frozen test 未运行。
- Gold、答案、runtime scope、active snapshot、E5、BM25、RRF 与 BGE reranker 均不变。
- 外部 planner 只接收用户明确授权的 dev 问题以及 scope 内论文 ID/标题。
- 指标为 strict current Chunk-ID Recall@10 / MRR；本实验不生成答案，因此没有 Citation P/R。
- `single_pass`、`bounded_refinement`、`routed_multi_search` 在同一只读 repeatable-read 事务中逐题运行。

## Provider 兼容性修复

当前 DeepSeek thinking 默认为开启。对一条 cross-paper dev 题的独立 probe 中，600、1024、
2048、4096 completion tokens 均被 reasoning 消耗，`content` 为空且 `finish_reason=length`。
仅对结构化 planner/rewrite 发送 `reasoning_effort=none` 后，同一 routing 请求使用 351
prompt tokens、107 completion tokens 返回合法 JSON。最终回答不受该参数影响。
DeepSeek 官方说明 `reasoning_effort=none` 可关闭 Chat Completions thinking：
<https://api-docs.deepseek.com/guides/thinking_mode/>。

初始未关闭 thinking 的运行中，bounded planner 19/37 次解析失败，六道多文档题的 router
6/6 失败，实际 routed search 为 0；该轮仅作为兼容性诊断，不作为策略 A/B。

## 路由门槛选择

在非自适应 route-only 版本中：

| min confidence | Routed Recall@10 | Routed MRR | 平均检索次数 | 多检索率 |
| ---: | ---: | ---: | ---: | ---: |
| 0.65 | 0.3739 | 0.3762 | 1.0541 | 0.0270 |
| 0.30 | 0.3739 | 0.3726 | 1.2703 | 0.1351 |

0.30 使更多 route 执行，但没有增加 Recall，且检索成本更高、MRR 更低。因此保留 0.65，
不根据“触发次数更多”降低安全门槛。

## 最终自适应 workflow

- 单文档 scope：直接使用 bounded refinement，不调用 document router。
- 多文档 scope：先生成最多三条单文档 route；每条 route 保持原 scope 真子集。
- route 低置信度：回退 bounded refinement；plan 无法解析/越界：保留 primary。
- primary 与成功 route 只使用 rank-only RRF 合并，并保留每个成功 route 的真实候选。

同一配置独立重复两次：

| Workflow | Recall@10 run 1 | Recall@10 run 2 | Recall 均值 | MRR run 1 | MRR run 2 | MRR 均值 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single_pass | 0.3649 | 0.3649 | 0.3649 | 0.3672 | 0.3672 | 0.3672 |
| bounded_refinement | 0.4369 | 0.4369 | 0.4369 | 0.3616 | 0.3616 | 0.3616 |
| routed_multi_search | 0.4640 | 0.4234 | 0.4437 | 0.3664 | 0.3609 | 0.3636 |

相对 single-pass，adaptive routed 的两次 Recall@10 分别增加 9.91pp 和 5.86pp，均值增加
7.88pp；但两次差异较大，不能只选最好的一次。bounded refinement 的两次结果完全一致，
Recall 增加 7.21pp，但 MRR 下降 0.56pp。

六道 cross-paper dev 子集在两次重复中结果一致：

| Workflow | Recall@10 | MRR | 平均检索次数 |
| --- | ---: | ---: | ---: |
| single_pass | 0.1389 | 0.1019 | 1.00 |
| bounded_refinement | 0.2500 | 0.1019 | 1.67 |
| routed_multi_search | 0.2500 | 0.1074 | 1.67 |

路由在 cross-paper Recall 上与 bounded refinement 持平，MRR 小幅增加 0.56pp；尚不能证明它
稳定提升 Recall。两次 adaptive 运行的 planner fallback rate 均为 0.1351，原因仅为
`DOCUMENT_ROUTE_LOW_CONFIDENCE` 或 `DOCUMENT_ROUTE_PLAN_FAILED`，没有检索、embedding、
rerank 硬降级。

## 结论

1. 关闭结构化 planner thinking 是有效且必要的兼容性修复。
2. bounded refinement 是当前更稳定的 dev 改进；adaptive routing 的平均 Recall 更高，但方差明显。
3. `routed_multi_search` 继续保留为 opt-in；默认仍为 `single_pass`，不运行 frozen test、不切换发布配置。
4. 下一步若继续，应先让 route plan 输出确定性提高并重复 dev；锁定方案后才能运行一次 frozen test
   和 Full Pipeline Citation P/R，不能按 test 结果反向调参。

## 后续本地诊断

- 六道 cross-paper dev 上，确定性逐文档同 query 的 Recall/MRR 为 0.1944/0.1333；标题追加
  降至 0.1389/0.0655，逐文档 wide80 仍为 0.1944 且 MRR 略降。将 bounded 结果与逐文档
  结果离线 RRF 后 Recall 仍为 0.2500，仅 MRR 从 0.1019 增至 0.1208。额外两次检索没有
  Recall 收益，因此不进入生产。
- 四道 multi-turn dev 的纯本地检索中，当前追问、用户历史拼接、冻结 expected standalone
  query 的 Recall@10 均为 0.2500；oracle rewrite 也没有增加 Recall。因此不根据 dev oracle
  修改 rewrite 内容，只修复结构化 rewrite 的 thinking 兼容性。
- 首次 Full Pipeline 的 37 道 answerable dev 中，17 道检索命中 Gold，14 道 citation 命中。
  三道“检索命中但 citation 未命中”的 Gold rank 为 2、9、10；其中一题引用了 rank 1–8
  和 10，却漏掉 rank 9。没有证据支持扩大 citation 正确范围或用更多 marker 修饰低质量排序。
