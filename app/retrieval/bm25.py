from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.retrieval.analyzer import Analyzer, SimpleAnalyzer


@dataclass
class BM25Stats:
    """Immutable corpus statistics for BM25 scoring (spec §13.2)."""

    k1: float = 1.5
    b: float = 0.75
    n_docs: int = 0
    avgdl: float = 0.0
    df: dict[str, int] = field(default_factory=dict)
    doc_len: dict[int, int] = field(default_factory=dict)
    doc_ids: list[int] = field(default_factory=list)
    # term -> {doc_id: tf}
    inverted_index: dict[str, dict[int, int]] = field(default_factory=dict)
    # doc_id -> list of tokens (for minimum_should_match)
    doc_tokens: dict[int, set[str]] = field(default_factory=dict)

    @property
    def idf(self) -> dict[str, float]:
        result: dict[str, float] = {}
        n = self.n_docs
        for term, df in self.df.items():
            result[term] = math.log((n - df + 0.5) / (df + 0.5) + 1)
        return result


class BM25Index:
    """BM25 index with build, search, save, load (spec §13.2).

    Okapi BM25 with k1=1.5, b=0.75.
    IDF: log((N - df + 0.5) / (df + 0.5) + 1)
    """

    def __init__(self, stats: BM25Stats | None = None, analyzer: Analyzer | None = None) -> None:
        self.stats = stats or BM25Stats()
        self._analyzer = analyzer or SimpleAnalyzer()
        self._idf_cache: dict[str, float] | None = None

    @property
    def idf(self) -> dict[str, float]:
        if self._idf_cache is None:
            self._idf_cache = self.stats.idf
        return self._idf_cache

    def build(
        self,
        docs: list[tuple[int, str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """Build BM25 stats from (doc_id, text) pairs.

        Recalculates all N, df, doc_len, avgdl from scratch (spec §13.2).
        """
        self.stats = BM25Stats(k1=k1, b=b)
        total_len = 0
        for doc_id, text in docs:
            tokens = self._analyzer.analyze(text)
            tf = Counter(tokens)
            dl = len(tokens)
            self.stats.doc_len[doc_id] = dl
            self.stats.doc_ids.append(doc_id)
            self.stats.doc_tokens[doc_id] = set(tokens)
            total_len += dl
            for term, count in tf.items():
                self.stats.df[term] = self.stats.df.get(term, 0) + 1
                self.stats.inverted_index.setdefault(term, {})[doc_id] = count
        self.stats.n_docs = len(docs)
        self.stats.avgdl = total_len / len(docs) if docs else 0.0
        self._idf_cache = None

    def search(
        self,
        query: str,
        *,
        top_k: int = 30,
        scope_doc_ids: set[int] | None = None,
        minimum_should_match: int = 1,
    ) -> list[tuple[int, float]]:
        """Search for documents matching *query*. Returns [(doc_id, score), ...].

        minimum_should_match: candidate must hit at least
        min(minimum_should_match, unique_query_term_count) unique query terms.
        """
        query_tokens = self._analyzer.analyze(query)
        unique_q_terms = set(query_tokens)
        min_match = min(minimum_should_match, len(unique_q_terms)) if unique_q_terms else 1

        # Collect candidates from inverted index.
        candidates: dict[int, dict[str, int]] = {}
        for term in unique_q_terms:
            postings = self.stats.inverted_index.get(term, {})
            for doc_id, tf in postings.items():
                if scope_doc_ids is not None and doc_id not in scope_doc_ids:
                    continue
                candidates.setdefault(doc_id, {})[term] = tf

        scores: list[tuple[int, float]] = []
        idf = self.idf
        k1 = self.stats.k1
        b = self.stats.b
        avgdl = self.stats.avgdl

        for doc_id, term_hits in candidates.items():
            # Check minimum_should_match.
            if len(term_hits) < min_match:
                continue
            dl = self.stats.doc_len.get(doc_id, 0)
            score = 0.0
            for term, tf in term_hits.items():
                idf_val = idf.get(term, 0.0)
                denom = tf + k1 * (1 - b + b * dl / avgdl) if avgdl > 0 else tf + k1
                score += idf_val * (tf * (k1 + 1)) / denom
            scores.append((doc_id, score))

        # Sort by score desc, then doc_id asc.
        scores.sort(key=lambda x: (-x[1], x[0]))
        return scores[:top_k]

    def to_dict(self) -> dict[str, Any]:
        return {
            "k1": self.stats.k1,
            "b": self.stats.b,
            "n_docs": self.stats.n_docs,
            "avgdl": self.stats.avgdl,
            "df": self.stats.df,
            "doc_len": {str(k): v for k, v in self.stats.doc_len.items()},
            "doc_ids": self.stats.doc_ids,
            "inverted_index": {
                term: {str(k): v for k, v in postings.items()}
                for term, postings in self.stats.inverted_index.items()
            },
            "doc_tokens": {str(k): list(v) for k, v in self.stats.doc_tokens.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], analyzer: Analyzer | None = None) -> BM25Index:
        stats = BM25Stats(
            k1=d.get("k1", 1.5),
            b=d.get("b", 0.75),
            n_docs=d.get("n_docs", 0),
            avgdl=d.get("avgdl", 0.0),
            df=d.get("df", {}),
            doc_len={int(k): v for k, v in d.get("doc_len", {}).items()},
            doc_ids=d.get("doc_ids", []),
            inverted_index={
                term: {int(k): v for k, v in postings.items()}
                for term, postings in d.get("inverted_index", {}).items()
            },
            doc_tokens={int(k): set(v) for k, v in d.get("doc_tokens", {}).items()},
        )
        return cls(stats=stats, analyzer=analyzer)
