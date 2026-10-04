# Standard-v2 Candidate Promotion and Citation Dev Diagnosis

Date: 2026-10-05

Branch: `codex/cjk-english-dual-query`

Starting gate commit: `13514e7`

Scope: standard-v2 answerable dev only (37 questions)

Frozen test evaluated: **no**

Gold, benchmark, parser/chunker, embedding/index, reranker, BM25, document routing,
translation prompt and generation prompt were not changed. Production defaults remain off.

## Candidate Promotion

The contemporary baseline and M variants shared one translation plan per question. The baseline
retains the historical rescue behavior and therefore sends a mean 40.8 candidates to the reranker;
M=2/3/5 apply source-aware reservation and a strict total reranker depth of 30.

| Variant | Recall@10 | MRR | nDCG@10 | Reranker-input recall | LOST_BEFORE_RERANK_DEPTH | LOST_IN_RERANK | FINAL_TOP10_HIT | Mean pool | Mean latency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | 0.5045 | 0.4298 | 0.4097 | 0.6351 | 7 | 4 | 23 | 40.8 | 2000.3 ms |
| M=2 | 0.4865 | 0.4280 | 0.4016 | 0.5766 | 9 | 3 | 22 | 30.0 | 1919.8 ms |
| M=3 | 0.4730 | 0.4153 | 0.3920 | 0.5766 | 10 | 3 | 21 | 30.0 | 1907.5 ms |
| M=5 | 0.4820 | 0.4228 | 0.3993 | 0.5991 | 9 | 3 | 22 | 30.0 | 1904.0 ms |

The locked historical dev result for the same baseline policy was Recall@10 0.5045, MRR 0.4062
and nDCG@10 0.3912. The contemporary baseline keeps Recall@10 and final-hit count unchanged but
shows rank-level variation; comparisons among M variants therefore use the same-run baseline above.

For the seven contemporary baseline depth losses, the best source was Dense-English for 2 cases and
BM25-English for 5. Their best per-source Gold ranks were 6, 6, 13, 19, 37, 76 and 82. None was in a
source top-5, so M=2/3/5 could not directly promote these Gold chunks. The strict 30 cap instead
displaced useful RRF/rescue candidates, reducing input recall and final Recall@10.

Decision: **PROMOTION: REJECT**. The implementation remains available only behind the default-off
experiment switch; no production default changes.

## Citation Diagnosis

The earlier frozen-gate stage split used retrieval-only Top10 as a proxy for generation context.
That proxy is not valid: the live evaluator issued a separate search request after chat, and the old
prediction artifact did not retain the source list actually packed for generation. Citation P/R
itself remains a direct Gold-versus-cited-ID measurement, but the old “retrieval hit + citation miss”
attribution is not auditable.

The evaluator now records `context_chunk_ids` from the chat response. A fresh answerable-dev-only run
produced the following actual-context decomposition:

| Outcome | Count |
| --- | ---: |
| Gold absent from actual generation context | 21 |
| Gold in context + citation hit | 13 |
| Gold in context + citation miss | 3 |

The three context-hit/citation-miss cases were `eval-024`, `eval-041` and `eval-048`.

| Classification (non-exclusive) | Count |
| --- | ---: |
| GOLD_IN_CONTEXT_NOT_CITED | 3 |
| WRONG_CONTEXT_CITED | 0 |
| OVER_CITATION | 0 |
| MISSING_CITATION | 3 |

Two cases returned the normalized insufficient-evidence refusal despite Gold appearing at actual
context rank 1 or 2. The remaining case produced a substantive answer but emitted `[Source N]`
markers, which are invalid under the structured `[N]` citation contract and therefore yielded no
validated citations. Per-question question/answer, Gold IDs/ranks, cited content and missed Gold
content are stored only in the ignored private diagnostic artifact.

A claim-evidence post-filter cannot repair these observed failures: it can remove unsupported
citations, but all three samples have no validated citation to filter, and two are false refusals.
The next experiment should first address citation-marker emission/normalization and refusal behavior,
then remeasure actual-context failures before adding claim-evidence filtering.

Decision: **CITATION GROUNDING: NOT YET**.

## Artifacts

- Private promotion result: `eval/results/standard-v2-candidate-promotion-dev-strict30-2026-10-05/dev-results.json`
- Private promotion table: `eval/results/standard-v2-candidate-promotion-dev-strict30-2026-10-05/dev-report.md`
- Private dev generation predictions: `eval/results/standard-v2-citation-grounding-dev-live-2026-10-05/predictions.json`
- Private citation diagnostic: `eval/results/standard-v2-citation-grounding-dev-live-2026-10-05/diagnostic-v2.json`

All four runtime artifacts remain ignored and must not be committed because they contain private
benchmark or paper-derived data.
