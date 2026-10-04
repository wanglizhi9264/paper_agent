# Standard-v2 frozen gate（2026-10-04）

Candidate implementation `2231190` was evaluated without changing retrieval parameters, Gold,
benchmark, models, index, parser/chunker or prompts. The locked configuration is recorded in
[`frozen-test-candidate-2026-10-04.md`](./frozen-test-candidate-2026-10-04.md).

The frozen retrieval run evaluated the 15 answerable test questions exactly once. Full Pipeline
then evaluated all 60 questions with the same candidate. An initial Full Pipeline transport attempt
was invalid because all requests were intercepted before reaching the loopback API; its all-zero
output is excluded. The valid run changed only `NO_PROXY=127.0.0.1,localhost`, completed 60/60 and
had zero prediction errors.

## Frozen Retrieval

| Metric | Dev | Frozen Test |
| --- | ---: | ---: |
| Recall@1 | 21.17% | 33.33% |
| Recall@3 | 36.04% | 56.67% |
| Recall@5 | 40.99% | 56.67% |
| Recall@10 / final rerank R@10 | 50.45% | 56.67% |
| MRR | 0.4062 | 0.5000 |
| nDCG@5 | 0.3597 | 0.5004 |
| nDCG@10 | 0.3912 | 0.5004 |
| Candidate Union@30 | 72.52% | 74.44% |
| Candidate Union@50 | 76.58% | 94.44% |
| Candidate Union@100 | 86.04% | 96.67% |
| RRF@30 | 57.66% | 66.67% |
| RRF@50 | 66.22% | 84.44% |
| Mean latency | 2.165s | 2.521s |

Failure stages count answerable questions, not individual evidence groups:

| Stage | Dev | Frozen Test |
| --- | ---: | ---: |
| RAW_UNION_MISS | 2 | 0 |
| LOST_IN_FUSION | 2 | 0 |
| LOST_BEFORE_RERANK_DEPTH | 7 | 5 |
| LOST_IN_RERANK | 3 | 1 |
| FINAL_TOP10_HIT | 23 | 9 |

Frozen retrieval completed without dependency degradation and generalized above the selected dev
R@10/MRR/nDCG@10. It still misses the release target: most remaining frozen loss occurs before the
fixed reranker depth, despite raw union and RRF containing the evidence at larger depths.

## Full Pipeline

Retrieval metrics below use the locked candidate traces from the dev selection and the one-shot
frozen retrieval. Citation, rejection and end-to-end latency use the valid 60-question live chat
run. They must not be confused with the legacy evaluator's follow-up `/search`, which does not run
chat-only cross-language translation.

| Metric | Dev | Frozen Test | Overall |
| --- | ---: | ---: | ---: |
| Retrieval Recall@1 | 21.17% | 33.33% | 24.68% |
| Retrieval Recall@3 | 36.04% | 56.67% | 41.99% |
| Retrieval Recall@5 | 40.99% | 56.67% | 45.51% |
| Retrieval Recall@10 | 50.45% | 56.67% | 52.24% |
| MRR | 0.4062 | 0.5000 | 0.4332 |
| nDCG@10 | 0.3912 | 0.5004 | 0.4227 |
| Citation Precision | 18.52% | 22.22% | 19.30% |
| Citation Recall | 40.00% | 40.91% | 40.21% |
| Unanswerable rejection | 100.00% | 100.00% | 100.00% |
| Prediction errors | 0 | 0 | 0 |
| Mean end-to-end latency | 11.827s | 10.904s | 11.550s |

| Metric | Previous Baseline | New Candidate |
| --- | ---: | ---: |
| Recall@10 | 38.78% | 52.24% |
| Citation Precision | 20.33% | 19.30% |
| Citation Recall | 29.90% | 40.21% |
| Unanswerable rejection | 87.50% | 100.00% |
| End-to-end latency | 9.718s | 11.550s |

## Citation diagnostics

| Category | Dev | Frozen Test | Overall |
| --- | ---: | ---: | ---: |
| Retrieval miss | 14 | 6 | 20 |
| Retrieval hit + citation miss | 3 | 2 | 5 |
| Retrieval hit + citation hit | 20 | 7 | 27 |

The five retrieval-hit/citation-miss cases are `eval-012`, `eval-042`, `eval-048`, `holdout-011`
and `holdout-015`. Exact Gold ranks, selected context ranks, generated citation Chunk IDs and missed
Gold Chunk IDs remain in the ignored local `full-gate-score.json`; private labels are not committed.
These cases prove citation selection is an independent bottleneck. Citation Precision also decreased
slightly because generated answers cite many non-Gold context chunks.

## Failure analysis

- Candidate retrieval is no longer the dominant frozen-test loss: raw union reaches 96.67% at 100.
- Fusion preserves frozen evidence well enough to reach 84.44% at RRF@50; no frozen question is
  classified as a fusion-only loss.
- Ranking/promotion is the main retrieval bottleneck: five frozen questions lose evidence before the
  fixed rerank input and one more is ranked out by the reranker.
- Citation selection is independently weak: five answerable questions retrieve Gold but do not cite
  it, and overall Citation Precision/Recall remain 19.30%/40.21%.

The candidate improves retrieval, Citation Recall and rejection over the previous baseline, but it
does not pass the approved release thresholds of Recall@10 >= 0.85, Citation Precision >= 0.95 and
Citation Recall >= 0.85. Frozen results must not be used for further tuning.

## Decision

**FAIL FROZEN GATE**
