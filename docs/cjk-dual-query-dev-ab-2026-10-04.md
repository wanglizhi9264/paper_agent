# CJK→English Dual-query Retrieval：standard-v2 dev A/B

日期：2026-10-04。该实验只使用 frozen standard-v2 的 37 道 answerable dev；frozen test
未运行，答案生成和 Citation 指标未运行。两组使用同一 active snapshot、Gold、scope、
multilingual-e5-base、BM25 index、BGE reranker 和默认生产深度 Dense/BM25/RRF=30/30/30。
Top-100 只作为独立 failure analysis，不参与下表最终生产指标。

## 生产深度结果

| Variant | R@1 | R@3 | R@5 | R@10 | MRR | nDCG@5 | nDCG@10 | 平均端到端 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| off | 0.2117 | 0.3288 | 0.3288 | 0.3694 | 0.3695 | 0.3112 | 0.3264 | 836.5 ms |
| dual | 0.2117 | 0.3468 | 0.4189 | 0.4865 | 0.4080 | 0.3605 | 0.3829 | 2781.5 ms |

dual 的 R@10 提升 11.71 个百分点，MRR 提升 0.0385；平均端到端延迟增加约
1944.9 ms（约 3.32 倍），其中 English query 生成平均 1815.9 ms。37 题 translation
fallback 为 0。

## Candidate 与 Gold rank 诊断

| Variant / stage | R@30 | R@50 | R@100 | Gold rank median | 已观察 Gold / 66 |
| --- | ---: | ---: | ---: | ---: | ---: |
| off raw union | 0.4550 | 0.5450 | 0.6216 | — | — |
| dual raw union | 0.7252 | 0.7658 | 0.8604 | — | — |
| Dense-original | 0.3423 | 0.4234 | 0.5090 | 14 | 31 |
| Dense-English | 0.4279 | 0.5541 | 0.6486 | 19 | 42 |
| BM25-original | 0.3559 | 0.4234 | 0.5090 | 6 | 34 |
| BM25-English | 0.5000 | 0.6036 | 0.7523 | 11 | 52 |

English Dense/BM25 明显扩大了可观察 Gold 数量。新增命中的低排名 Gold 也进入均值/中位数，
因此不能只用 median 判断 English list；Recall 与 observed count 才是主要解释。当前 hard slice
中有 4 题的 BM25-original 在 scope 内返回空列表，BM25-English 均找到了至少一个 Gold，
说明英文 sparse query 确实覆盖了中文 token overlap=0/zero-candidate 的一部分问题。

## 13 个既有 candidate miss

- raw 四路 union：per-list top-30 恢复 8/13，top-50 恢复 8/13；
- multi-list RRF：top-30 恢复 5/13，top-50 恢复 6/13；
- 默认生产 rerank top-10：最终恢复 3/13。

四路候选有效，但融合与排序仍有损失。全 dev 中有 5 题已进入 raw union top-30，却未进入
RRF top-30（`eval-007/033/041/044/046`）；另有 4 题已进入 RRF top-30，却未进入最终
rerank top-10（`eval-002/005/013/015`）。生产深度下 RRF R@10 从 0.3649 降到 0.3423，
但 RRF R@30 从 0.4099 升到 0.5991，随后 reranker 将 R@10 提升到 0.4865。这说明
multi-RRF 没有整体丢掉新增候选，但四路等权会把部分 Gold 向后推，reranker 只恢复了其中一部分。

## 结论

**KEEP OPT-IN**。预注册规则要求 rerank R@10 上升、MRR 回退不超过 0.01、translation
failure 不超过 20%；本次三项均通过。由于额外延迟接近 2 秒、13 个 hard miss 最终只恢复
3 个、Citation 尚未评测，production default 继续保持 `off`，不自动切换。

原始逐题/逐 Gold 结果保存在本机 ignored 目录
`eval/results/standard-v2-cjk-dual-dev-2026-10-04/`，不提交私有 query、派生标签或运行结果。
