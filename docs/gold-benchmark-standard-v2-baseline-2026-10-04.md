# Gold benchmark standard v2 first baseline — 2026-10-04

This report contains aggregate, non-evidence-bearing results only. Private questions,
answers, evidence text, PDFs, runtime labels, and raw predictions remain ignored.

## Frozen evaluation conditions

- Leakage-controlled standard v2: 60 questions, 42 dev / 18 frozen test
- 52 answerable / 8 unanswerable; all positive evidence resolved before evaluation
- Dev/test share zero stable evidence anchors, physical source pages, or resolved Chunks
- Active snapshot: `2d0e23b3-da8c-4631-aceb-85cf0c656167`
- Generator: `deepseek-v4-flash`; query language policy: default `preserve`
- The frozen test was not used for retrieval tuning before this run
- One `eval-022` request failed with a transient `HTTPStatusError`; only that item was
  retried. The other 59 predictions were retained unchanged. The completed run has zero
  prediction errors, and the retry is recorded beside the ignored raw outputs.

## Retrieval ablation

| Pipeline | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@5 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense only | 11.54% | 23.72% | 24.36% | 27.24% | 21.11% | 19.97% | 20.90% |
| BM25 only | 9.29% | 17.63% | 23.40% | 28.53% | 20.32% | 18.73% | 20.47% |
| Dense + BM25 + RRF | 10.58% | 20.83% | 23.72% | 33.65% | 21.67% | 19.13% | 22.62% |
| Rerank | 24.68% | 35.90% | 35.90% | 38.78% | 39.11% | 34.40% | 35.49% |
| Selected context | 24.68% | 35.90% | 35.90% | 38.78% | 38.95% | 34.40% | 35.49% |

Mean production search latency was 1,349 ms.

## Full pipeline

| Metric | Standard v2 baseline | Release threshold | Passed |
| --- | ---: | ---: | --- |
| Recall@1 | 22.76% | — | — |
| Recall@3 | 33.97% | — | — |
| Recall@5 | 33.97% | — | — |
| Recall@10 | 38.78% | 85.00% | No |
| MRR | 37.22% | — | — |
| nDCG@5 | 32.48% | — | — |
| nDCG@10 | 34.12% | — | — |
| Citation Precision | 20.33% | 95.00% | No |
| Citation Recall | 29.90% | 85.00% | No |
| Unanswerable rejection | 87.50% | 80.00% | Yes |
| Prediction errors | 0 | 0 | Yes |
| Mean end-to-end latency | 9,718 ms | — | — |

Dev Recall@10 was 36.94%; frozen-test Recall@10 was 43.33%. All three newly authored
test negatives were rejected correctly. The one failed negative was a retained legacy dev
sample where the model produced a non-canonical refusal followed by irrelevant citations.

## Error concentration

| Question type | Recall@10 | Citation Precision | Citation Recall |
| --- | ---: | ---: | ---: |
| Experiment/table | 75.00% | 42.31% | 63.16% |
| Cross-section synthesis | 47.92% | 20.00% | 40.00% |
| Method/mechanism | 40.00% | 22.73% | 33.33% |
| Fact/definition | 30.00% | 13.64% | 23.08% |
| Multi-turn rewrite | 16.67% | 0.00% | 0.00% |
| Cross-paper comparison | 10.42% | 0.00% | 0.00% |

The dominant failures remain cross-paper retrieval, conversational rewrite, and citation
selection. Reranking substantially improves ordering over RRF, but it cannot recover evidence
missing from the fused candidate set.

## Relation to the legacy benchmark

The latest legacy `preserve` run reported Recall@10 38.30%, Citation Precision 20.00%,
Citation Recall 31.43%, and unanswerable rejection 87.50%. Standard v2 reports 38.78%,
20.33%, 29.90%, and 87.50%, respectively. These small numerical differences must not be
claimed as retrieval improvement because 18 test questions and their gold evidence changed,
and the legacy split had cross-split leakage. This document establishes the new baseline.

The still older pre-benchmark-repair baseline (Recall@10 36.65%, Citation Precision
20.16%, Citation Recall 24.27%) is even less directly comparable because its runtime evidence
labels were not yet reliably aligned.
