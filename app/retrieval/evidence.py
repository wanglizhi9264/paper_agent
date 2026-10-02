"""Pure, conservative evidence selection; no DB, model, or parser dependencies."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from uuid import UUID

from app.rerank.base import RerankError

RankedCandidate = tuple[int, float, str]
TableKey = tuple[UUID, UUID, str]


@dataclass(frozen=True)
class EvidenceCandidate:
    faiss_id: int
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    content_hash: str
    table_id: str | None = None
    cell_ids: frozenset[UUID] = frozenset()

    @property
    def table_key(self) -> TableKey | None:
        if not self.table_id:
            return None
        return (self.document_id, self.document_version_id, self.table_id)


@dataclass(frozen=True)
class SelectionDecision:
    chunk_id: UUID
    reason: str
    covered_by: list[UUID]


@dataclass
class SelectionResult:
    selected: list[RankedCandidate] = field(default_factory=list)
    decisions: list[SelectionDecision] = field(default_factory=list)


def rank_candidates(fused: list[RankedCandidate], scores: list[float]) -> list[RankedCandidate]:
    if len(fused) != len(scores) or any(not math.isfinite(score) for score in scores):
        raise RerankError("Reranker returned invalid scores")
    return sorted(
        [(row[0], score, "rerank") for row, score in zip(fused, scores, strict=True)],
        key=lambda row: (-row[1], row[0]),
    )


def select_evidence(
    ranked: list[RankedCandidate],
    candidates: dict[int, EvidenceCandidate],
    *,
    top_k: int,
    strategy: str = "legacy",
    balance_document_ids: frozenset[UUID] = frozenset(),
) -> SelectionResult:
    """Keep actual scored chunks. Cell coverage is not a relevance judgment.

    Only previously selected chunks contribute coverage. In particular an
    unselected parent/raw-text alias cannot suppress a row, and different
    columns of the same row must survive unless their cells are covered.
    """
    if top_k < 1 or strategy not in {"legacy", "cell_coverage"}:
        raise ValueError("Invalid evidence selection policy")
    if len(balance_document_ids) > top_k:
        raise ValueError("Document balance requires at least one slot per document")
    pool = _deduplicate_ranked(
        ranked,
        candidates,
        top_k=len(ranked) if balance_document_ids else top_k,
        strategy=strategy,
        preserve_document_ids=balance_document_ids,
    )
    if not balance_document_ids:
        return pool

    first_by_document: dict[UUID, RankedCandidate] = {}
    for row in pool.selected:
        document_id = candidates[row[0]].document_id
        if document_id in balance_document_ids:
            first_by_document.setdefault(document_id, row)
    mandatory_ids = {row[0] for row in first_by_document.values()}
    selected_ids = set(mandatory_ids)
    for row in pool.selected:
        if len(selected_ids) >= top_k:
            break
        selected_ids.add(row[0])
    selected = [row for row in pool.selected if row[0] in selected_ids]
    return SelectionResult(selected=selected[:top_k], decisions=pool.decisions)


def _deduplicate_ranked(
    ranked: list[RankedCandidate],
    candidates: dict[int, EvidenceCandidate],
    *,
    top_k: int,
    strategy: str,
    preserve_document_ids: frozenset[UUID] = frozenset(),
) -> SelectionResult:
    result = SelectionResult()
    seen_ids: dict[UUID, UUID] = {}
    seen_hashes: dict[tuple[str, ...], UUID] = {}
    covered: dict[TableKey, dict[UUID, UUID]] = {}
    for row in ranked:
        candidate = candidates[row[0]]
        hash_key = (candidate.content_hash,)
        if strategy == "cell_coverage" or candidate.document_id in preserve_document_ids:
            hash_key = (
                str(candidate.document_id),
                str(candidate.document_version_id),
                candidate.table_id or "",
                candidate.content_hash,
            )
        reason = ""
        owners: list[UUID] = []
        if candidate.chunk_id in seen_ids:
            reason, owners = "duplicate_chunk", [candidate.chunk_id]
        elif hash_key in seen_hashes:
            reason, owners = "duplicate_hash", [seen_hashes[hash_key]]
        else:
            seen_ids[candidate.chunk_id] = candidate.chunk_id
            # A covered alias must not claim ownership of future evidence.
            table_key = candidate.table_key
            coverage = covered.get(table_key, {}) if table_key is not None else {}
            if (
                strategy == "cell_coverage"
                and candidate.cell_ids
                and candidate.cell_ids.issubset(coverage)
            ):
                reason = "cells_covered"
                owners = sorted({coverage[cell] for cell in candidate.cell_ids}, key=str)
            else:
                seen_hashes[hash_key] = candidate.chunk_id
                result.selected.append(row)
                if table_key is not None:
                    cells = covered.setdefault(table_key, {})
                    for cell in candidate.cell_ids:
                        cells.setdefault(cell, candidate.chunk_id)
        if reason:
            result.decisions.append(SelectionDecision(candidate.chunk_id, reason, owners))
        if len(result.selected) == top_k:
            break
    return result
