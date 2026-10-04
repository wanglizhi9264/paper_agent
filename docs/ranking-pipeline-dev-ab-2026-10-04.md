# Standard-v2 dev dual-query ranking A/B（2026-10-04）

本报告只包含 leakage-controlled standard-v2 的 37 道 answerable dev 聚合结果。Frozen test 未
运行，Gold、问题、scope、active snapshot、parser/chunker、index、embedding、BM25 analyzer、
routing、citation、generation 和 translation prompt 均未修改。原始逐题诊断保存在本机忽略的
`eval/results/standard-v2-ranking-pipeline-dev-2026-10-04/`，不提交私有问题或论文正文。

## 实验结果

`P0` 是 source/fusion/rerank depth 均为 30 的既有 dual-query baseline。Stage A 为便于观察
Top-100 候选将 source/fusion depth 固定为 100，rerank depth 仍为 30；后续阶段只继承前一阶段
胜者。`B1/B2` 实际继承 A0 four-way RRF，因为 best-rank 并未胜出。

| Variant | R@1 | R@3 | R@5 | R@10 | MRR | nDCG@10 | Raw union R@30/50/100 | Fused R@10/30/50 | Pool | Hard13 | Total ms | Rerank ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |
| P0 dual baseline | .2117 | .3468 | .3964 | .4640 | .3894 | .3715 | .7252/.7432/.7432 | .3423/.5991/.6306 | 30.0 | 3/13 | 2168.3 | 224.5 |
| A0 four-way RRF | .2117 | .3604 | .4009 | .4865 | .4037 | .3825 | .7252/.7658/.8604 | .4009/.5766/.6622 | 35.7 | 4/13 | 2136.9 | 191.2 |
| A1 retriever best-rank RRF | .2117 | .3333 | .4144 | .4550 | .3917 | .3678 | .7252/.7658/.8604 | .4595/.5676/.6937 | 35.7 | 3/13 | 2126.9 | 184.1 |
| B1 A0 + rescue 5 | .2117 | .3604 | .4009 | .4865 | .4037 | .3825 | .7252/.7658/.8604 | .4009/.5766/.6622 | 38.0 | 4/13 | 2151.2 | 198.4 |
| B2 A0 + rescue 10 | .2117 | .3604 | .4099 | **.5045** | **.4062** | **.3912** | .7252/.7658/.8604 | .4009/.5766/.6622 | 40.9 | 4/13 | 2165.3 | 214.9 |
| C rerank 50 | .2117 | .3333 | .4189 | .4595 | .4126 | .3747 | .7252/.7658/.8604 | .4009/.5766/.6622 | 56.8 | 3/13 | 2241.0 | 295.4 |
| C rerank 80 | .2117 | .3333 | .3829 | .4459 | .4117 | .3661 | .7252/.7658/.8604 | .4009/.5766/.6622 | 82.6 | 3/13 | 2349.7 | 414.8 |
| C rerank 100 | .2117 | .3333 | .3829 | .4369 | .4087 | .3621 | .7252/.7658/.8604 | .4009/.5766/.6622 | 100.3 | 3/13 | 2434.0 | 491.0 |
| D B2 + English rerank | .2117 | .3288 | .3694 | .4414 | .3984 | .3643 | .7252/.7658/.8604 | .4009/.5766/.6622 | 40.9 | 3/13 | 2135.1 | 212.0 |

## 失败阶段

| Variant | Raw-union miss | Lost in fusion | Lost before depth | Lost in rerank | Final Top-10 hit |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0 | 6 | 2 | 3 | 5 | 21 |
| A0 | 2 | 2 | 8 | 3 | 22 |
| A1 | 2 | 2 | 8 | 4 | 21 |
| B1 | 2 | 2 | 8 | 3 | 22 |
| B2 | 2 | 2 | 7 | 3 | 23 |
| C50 | 2 | 2 | 4 | 7 | 22 |
| C80 | 2 | 2 | 2 | 9 | 22 |
| C100 | 2 | 2 | 2 | 9 | 22 |
| D English rerank | 2 | 2 | 7 | 5 | 21 |

## 结论

1. 四路 RRF 在个别候选上确有稀释，但没有形成“best-rank 必然更好”的整体证据。best-rank
   将 fused R@10 从 .4009 提至 .4595、R@50 从 .6622 提至 .6937，却使 final R@10 从 .4865
   降至 .4550，说明当前主要损失不能仅归因于四路投票结构。
2. top-10 rescue 有实际但有限的收益：candidate recall .5766→.6216，最终 R@10
   .4865→.5045；它将一题从 depth loss 带入 rerank，并将另一题从 rerank loss 恢复到 Top-10。
3. 扩大 rerank depth 只增加 pre-rerank coverage，没有带来最终收益。50/80/100 的 candidate
   recall 为 .6712/.7342/.7658，但 final R@10 反降为 .4595/.4459/.4369，rerank latency 从
   B2 的 214.9 ms 增至 295.4/414.8/491.0 ms。
4. English rerank 不优于 original Chinese rerank：R@10 .4414 对 .5045，MRR .3984 对
   .4062，hard-13 由 4 降至 3。
5. 新主瓶颈是 reranker 排序质量，而不是单纯候选数量。最优 B2 仍有 3 道题在已有候选时被
   reranker 排出 Top-10；再扩大输入会把 rerank loss 增至 7–9。另有 2 道 raw union miss、2 道
   fusion loss 和 7 道 depth loss，不能靠无界扩大 reranker pool 安全解决。

Decision: **RECOMMEND NEXT CANDIDATE FOR FROZEN-TEST GATE**。候选为四路 RRF、rescue top-10、
rerank depth 30、original query。Production defaults 保持 dual-query off、rescue off；未运行 test。
