# Retrieval Design

This document describes the hybrid retrieval pipeline of Paper RAG Assistant. For normative requirements, see [`spec.md`](spec.md) §13–15. For architecture overview, see [`architecture.md`](architecture.md).

## 1. Pipeline Overview

```
Query string
  │
  ├── 1. Scope Resolution (all / documents / collection)
  │
  ├── 2. Dense Retrieval
  │     embed query → FAISS search (top-30) → filter by scope
  │
  ├── 3. Sparse Retrieval
  │     BM25 search (top-30) → filter by scope
  │
  ├── 4. RRF Fusion
  │     reciprocal rank fusion (k=60) → top-30
  │
  ├── 5. Cross-encoder Rerank (graceful degradation)
  │     BGE reranker → re-sort by rerank score
  │
  └── 6. Top-k Selection → SearchResponse
```

### Key Invariants

1. **Scope applies to both paths before fusion.** Dense and BM25 both filter to the allowed document set before candidate collection. No global top-k then tail-filter. (RAG invariant #10)
2. **RRF rank starts at 1.** BM25 and cosine scores are never directly added. (RAG invariant #7)
3. **Rerank runs after fusion.** Neighbor/chapter expansion would run after rerank. (RAG invariant #8)
4. **`raw_content` for citation/prompt; `retrieval_content` for embedding/BM25.** Never mixed. (RAG invariant #1)

## 2. Dense Retrieval (FAISS)

### 2.1 Index Structure

- FAISS `IndexIDMap2(IndexFlatIP)` — inner product on L2-normalized vectors.
- Each chunk has a non-negative `int64` FAISS ID stored in `Chunk.faiss_id`.
- The index is corpus-wide (not per-document) and rebuilt on every new/reindexed document.
- Vectors are L2-normalized at embed time by the E5 adapter. Zero-norm vectors raise `ZeroVectorError`. (RAG invariant #4)

### 2.2 Query Embedding

```python
query_vector = embedding_provider.embed_query(request.query).vectors[0]
```

The E5 adapter adds `query: ` prefix automatically (RAG invariant #3). The adapter is responsible for prefixing — callers never prepend prefixes manually.

### 2.3 Search and Filter

```python
scores, ids = faiss.search(query_vector, top_k=faiss.ntotal)
dense = [(fid, score) for score, fid in zip(scores, ids) if fid in allowed][:30]
```

FAISS returns all results (or `ntotal` if smaller). Scope filtering removes any FAISS ID not in the allowed set. This ensures no out-of-scope document leaks into results.

## 3. Sparse Retrieval (BM25)

### 3.1 Index Structure

- Okapi BM25 with `k1=1.5`, `b=0.75`.
- IDF: `log((N - df + 0.5) / (df + 0.5) + 1)`.
- Inverted index: `term → {doc_id: tf}`.
- Doc tokens tracked for `minimum_should_match` filtering.
- Serialized as JSON (via `to_dict` / `from_dict`).

### 3.2 Analyzer

`SimpleAnalyzer` performs:
1. Lowercase
2. Unicode NFKC normalization
3. Split on non-letter/digit (string-based, not raw regex)

### 3.3 Search and Filter

```python
sparse = bm25.search(
    query,
    top_k=30,
    scope_doc_ids=allowed,     # filter before fusion
    minimum_should_match=request.minimum_should_match,
)
```

`minimum_should_match` (default 1) requires candidates to hit at least `min(minimum_should_match, unique_query_term_count)` unique query terms. This prevents high-IDF single-term matches from dominating.

### 3.4 Tie-breaking

BM25 results are sorted by:
1. Score descending
2. `doc_id` ascending

This ensures deterministic output across runs.

## 4. RRF Fusion

### 4.1 Formula

```
RRF_score(d) = Σ 1 / (k + rank_i(d))
```

- `k = 60` (standard RRF constant)
- `rank` starts at 1 (not 0)
- Each source (dense, BM25) contributes independently

### 4.2 Implementation

```python
for rank, (faiss_id, _score) in enumerate(dense_results, start=1):
    scores[faiss_id] += 1.0 / (k + rank)

for rank, (faiss_id, _score) in enumerate(bm25_results, start=1):
    scores[faiss_id] += 1.0 / (k + rank)
```

### 4.3 Tie-breaking

After RRF, results are sorted by:
1. RRF score descending
2. Best source rank ascending (prefer the path that ranked it higher)
3. FAISS ID ascending

This produces stable, reproducible rankings.

### 4.4 No Score Addition

BM25 scores (unbounded, document-length dependent) and cosine similarity (bounded [-1, 1]) are **never** directly added. RRF operates on ranks only. (RAG invariant #7)

## 5. Cross-encoder Rerank

### 5.1 Purpose

RRF fusion uses rank information only, not semantic relevance. The cross-encoder re-scores `(query, passage)` pairs jointly, producing more accurate relevance estimates.

### 5.2 Implementation

```python
reranker = get_reranker()  # BGEReranker in production, FakeReranker in test
passages = [by_faiss[fid].retrieval_content for fid, _, _ in fused]
rerank_scores = reranker.rerank(query, passages)
ranked = [(fid, score, "rerank") for (fid, _, _), score in zip(fused, rerank_scores)]
ranked.sort(key=lambda x: (-x[1], x[0]))
```

### 5.3 Graceful Degradation

If the reranker fails (model not available, OOM, etc.), the system falls back to the RRF order and adds `"RERANK_UNAVAILABLE"` to `degraded_reasons`. The search still returns results — it just doesn't have the rerank quality boost.

### 5.4 Model

- Production: `BAAI/bge-reranker-base` (FP16, batch=4, on GPU)
- Test: `FakeReranker` (token overlap, deterministic, no model download)

## 6. Context Engineering (Chat Pipeline)

When a chat request is received, the search pipeline runs first, then context is packed for the LLM.

### 6.1 Dedup

Order: **chunk_id first, then content_hash.** (RAG invariant #9)

```python
results = dedup_by_chunk_id(results)   # remove exact chunk duplicates
results = dedup_by_content_hash(results) # remove same-content chunks
```

### 6.2 Neighbor Expansion (after rerank)

For each result chunk, adjacent chunks in the same document and section are added with `source="expanded"` and `score=0.0`. The window is configurable (default 1).

### 6.3 Context Packing

```python
blocks = pack_context(
    results,
    budget_tokens=4292,    # 8192 total - 1200 system - 1200 history - 1500 answer
    tokens_per_char=0.25,  # approx 4 chars per token
    merge_adjacent=True,
)
```

Packing rules:
1. Iterate in rerank order.
2. Merge adjacent chunks in the same section (for continuity).
3. Truncate at sentence boundary if over budget.
4. Assign `[Source N]` markers (1-based).
5. Build citation map: `{index: chunk_id}`.

### 6.4 Citation Validation

```python
answer, citations, invalid = validate_citations(llm_response, citation_map)
```

1. Parse `[N]` markers from LLM output via regex.
2. Validate each against the citation map.
3. Strip invalid markers from the answer text.
4. Return valid citations as `[{index, chunk_id}]`.

Citations must map to actual packed sources with unique chunk IDs. No fabricated or dangling references. (RAG invariant #11)

## 7. Search API

### 7.1 Request

```json
{
  "query": "What is the main contribution?",
  "scope": {
    "type": "documents",
    "documentIds": ["uuid-1", "uuid-2"]
  },
  "topK": 8,
  "minimumShouldMatch": 1,
  "debug": false
}
```

### 7.2 Response

```json
{
  "originalQuery": "What is the main contribution?",
  "rewrittenQuery": "What is the main contribution?",
  "results": [
    {
      "chunkId": "uuid",
      "documentId": "uuid",
      "documentTitle": "Paper Title",
      "sectionPath": ["Introduction", "Contributions"],
      "pageStart": 1,
      "pageEnd": 2,
      "rawContent": "We propose ...",
      "score": 0.95,
      "rank": 1
    }
  ],
  "degradedReasons": [],
  "debug": null
}
```

### 7.3 Scope Types

| Scope | Behavior |
| --- | --- |
| `all` | Search all READY documents |
| `documents` | Search only specified document IDs (must all be READY) |
| `collection` | Search only documents in the specified collection (must be READY) |

Scope is resolved to a set of document UUIDs, then converted to FAISS ID sets. Both Dense and BM25 paths receive the same allowed set before search.

## 8. Evaluation

### 8.1 Metrics

- **Recall@K**: fraction of relevant chunks in top-K (K = 1, 3, 5, 10)
- **MRR**: Mean Reciprocal Rank
- **nDCG@K**: Normalized Discounted Cumulative Gain
- **Citation Precision**: valid citations / total citations
- **Citation Recall**: cited relevant chunks / all relevant chunks

### 8.2 Ablation Configs

| Config | Dense | BM25 | RRF | Rerank | Rewrite | Expansion |
| --- | --- | --- | --- | --- | --- | --- |
| `dense_only` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| `bm25_only` | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `dense_bm25_rrf` | ✓ | ✓ | ✓ | ✗ | ✗ | ✗ |
| `with_rerank` | ✓ | ✓ | ✓ | ✓ | ✗ | ✗ |
| `full_pipeline` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `no_rewrite` | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ |
| `no_expansion` | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ |

### 8.3 Acceptance Thresholds (Candidate)

| Metric | Threshold |
| --- | --- |
| Recall@10 | ≥ 0.85 |
| Citation Precision | ≥ 0.95 |
| Citation Recall | ≥ 0.85 |
| Unanswerable rejection | ≥ 0.80 |

First real baseline may not meet these. Results and error categories must be saved; no faked predictions or metrics.
