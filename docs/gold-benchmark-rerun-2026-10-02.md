# Gold benchmark rerun — 2026-10-02

This report contains aggregate, non-evidence-bearing results only. Private questions,
answers, evidence text, PDFs, raw predictions, and snapshot artifacts remain ignored.

## Frozen evaluation conditions

- 60 questions: 52 answerable and 8 unanswerable
- All 52 answerable questions had resolved evidence labels before evaluation
- The same active index snapshot, parser manifests, model manifest, scopes, and frozen
  question/answer/evidence labels were used for every run
- No label was changed based on retrieval output
- Both Full Pipeline runs completed 60/60 predictions with zero prediction errors

## Retrieval ablation

| Pipeline | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@5 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense only | 12.82% | 20.83% | 22.44% | 26.28% | 22.79% | 19.89% | 21.13% |
| BM25 only | 8.97% | 20.51% | 28.21% | 35.26% | 24.11% | 22.12% | 24.78% |
| Dense + BM25 + RRF | 8.33% | 20.83% | 23.72% | 36.54% | 23.17% | 19.22% | 23.75% |
| Rerank | 20.51% | 33.97% | 34.94% | 37.82% | 36.67% | 32.30% | 33.38% |
| Selected context | 20.51% | 33.97% | 34.94% | 38.30% | 36.42% | 32.30% | 33.60% |

## Full Pipeline A/B

| Metric | `preserve` | `english_for_cjk` |
| --- | ---: | ---: |
| Recall@1 | 20.51% | 19.55% |
| Recall@3 | 33.97% | 34.94% |
| Recall@5 | 34.94% | 38.78% |
| Recall@10 | 38.30% | 40.87% |
| MRR | 36.42% | 37.81% |
| nDCG@5 | 32.30% | 33.99% |
| nDCG@10 | 33.60% | 34.90% |
| Citation Precision | 20.00% | 19.44% |
| Citation Recall | 31.43% | 30.48% |
| Unanswerable rejection | 87.50% | 100.00% |
| Mean end-to-end latency | 9,384 ms | 14,131 ms |

The CJK-to-English rewrite improved Recall@10 by 2.56 percentage points and rejected
all eight unanswerable questions, but citation metrics decreased slightly and mean
latency increased by 4.75 seconds. The default therefore remains `preserve` pending
repeated runs and citation-quality work.

Neither configuration passed the release gate. The dominant remaining issue is
candidate recall: reranking improves ordering but cannot recover evidence absent from
the Dense/BM25 candidate pool.

The older pre-benchmark-repair baseline (Recall@10 36.65%, Citation Precision 20.16%,
Citation Recall 24.27%) is not directly comparable because its evidence labels were
not yet reliably aligned with the parsed PDFs and current chunks.
