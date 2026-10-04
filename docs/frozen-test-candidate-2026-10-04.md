# Standard-v2 frozen-test candidate（2026-10-04）

本文件在 frozen test 首次运行前锁定候选。Frozen test 结果不得用于修改本配置、Gold、
acceptable evidence、prompt、模型、索引、parser 或 chunker。

## Immutable provenance

| Field | Locked value |
| --- | --- |
| Candidate implementation commit | `2231190` |
| Branch | `codex/cjk-english-dual-query` |
| Active snapshot ID | `2d0e23b3-da8c-4631-aceb-85cf0c656167` |
| Benchmark | Paper RAG Private Benchmark Standard v2 |
| Benchmark version | `2.0.0-private-standard` |
| Embedding model | `intfloat/multilingual-e5-base` |
| Embedding revision | `d128750597153bb5987e10b1c3493a34e5a4502a` |
| Embedding signature | `c618f141c8be0bc3` |
| Reranker model | `BAAI/bge-reranker-base` |
| Reranker revision | `2cfc18c9415c912f9d8155881c133215df768a70` |
| Generator model/revision | `deepseek-v4-flash` |

## Locked retrieval configuration

```ini
chat_cross_language_retrieval = cjk_to_english_dual
retrieval_cross_language_fusion = four_way_rrf
retrieval_candidate_rescue = retriever_top_m
retrieval_candidate_rescue_top_m = 10
retrieval_rerank_top_k = 30
retrieval_rerank_query = original

retrieval_dense_top_k = 100
retrieval_bm25_top_k = 100
retrieval_rrf_top_k = 100
retrieval_selection = legacy
retrieval_document_balance = explicit_scope
chat_retrieval_workflow = single_pass
chat_rewrite_language = preserve
```

The 100/100/100 source and fusion depths are part of the previously selected dev candidate
(`B2_rescue_10`), not a frozen-test adjustment. Reranker input depth is independently fixed at 30;
rescue may add de-duplicated Dense/BM25 top-10 candidates. Final evaluation depth remains Top-10.

## Frozen constraints

- Keep the active FAISS/BM25 snapshot, embedding and reranker unchanged.
- Keep parser, chunker, stable Gold anchors, derived snapshot labels and benchmark questions unchanged.
- Keep cross-language translation and generation prompts unchanged.
- Run retrieval-only frozen test once, then—only if execution is complete and non-degraded—run the
  complete 60-question Full Pipeline with this exact configuration.
- Do not tune against or repeatedly inspect frozen-test outcomes.
