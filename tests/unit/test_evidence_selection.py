from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from app.rerank.base import RerankError
from app.retrieval.evidence import EvidenceCandidate, rank_candidates, select_evidence


def candidate(n: int, cells: tuple[int, ...] = (), **changes: object) -> EvidenceCandidate:
    base = EvidenceCandidate(
        faiss_id=n,
        chunk_id=UUID(int=n),
        document_id=UUID(int=100),
        document_version_id=UUID(int=200),
        content_hash=f"hash-{n}",
        table_id="table-a",
        cell_ids=frozenset(UUID(int=c) for c in cells),
    )
    return replace(base, **changes)


def choose(
    candidates: list[EvidenceCandidate],
    strategy: str = "cell_coverage",
    k: int = 10,
    balance_document_ids: frozenset[UUID] = frozenset(),
):
    return select_evidence(
        [(c.faiss_id, 1.0 / (i + 1), "rerank") for i, c in enumerate(candidates)],
        {c.faiss_id: c for c in candidates},
        top_k=k,
        strategy=strategy,
        balance_document_ids=balance_document_ids,
    )


def test_covered_group_removed_but_complementary_group_kept() -> None:
    result = choose([candidate(1, (10, 11)), candidate(2, (10, 11)), candidate(3, (10, 12))])
    assert [r[0] for r in result.selected] == [1, 3]
    assert result.decisions[0].reason == "cells_covered"
    assert result.decisions[0].covered_by == [UUID(int=1)]


def test_union_of_selected_groups_can_cover_row() -> None:
    result = choose([candidate(1, (10, 11)), candidate(2, (10, 12)), candidate(3, (10, 11, 12))])
    assert [r[0] for r in result.selected] == [1, 2]
    assert result.decisions[0].covered_by == [UUID(int=1), UUID(int=2)]


@pytest.mark.parametrize(
    "changes",
    [
        {"document_id": UUID(int=101)},
        {"document_version_id": UUID(int=201)},
        {"table_id": "table-b"},
        {"table_id": None},
    ],
)
def test_identity_boundaries_not_merged(changes) -> None:
    result = choose([candidate(1, (10,)), candidate(2, (10,), **changes)])
    assert len(result.selected) == 2


def test_missing_cells_and_raw_text_not_inferred_equivalent() -> None:
    result = choose([candidate(1), candidate(2), candidate(3, (10,))])
    assert len(result.selected) == 3


def test_legacy_preserves_cell_aliases() -> None:
    assert len(choose([candidate(1, (10,)), candidate(2, (10,))], "legacy").selected) == 2


def test_hash_dedup_scoped_to_document_and_table_in_new_strategy() -> None:
    a = candidate(1, content_hash="same")
    b = candidate(2, content_hash="same", document_id=UUID(int=101))
    c = candidate(3, content_hash="same", table_id="table-b")
    assert len(choose([a, b, c]).selected) == 3
    assert len(choose([a, b, c], "legacy").selected) == 1


def test_same_scope_hash_and_id_dedup_before_cells() -> None:
    result = choose(
        [candidate(1), candidate(2, content_hash="hash-1"), candidate(3, chunk_id=UUID(int=1))]
    )
    assert [d.reason for d in result.decisions] == ["duplicate_hash", "duplicate_chunk"]


def test_selection_preserves_scores_and_backfills_after_suppression() -> None:
    result = choose([candidate(1, (10,)), candidate(2, (10,)), candidate(3, (11,))], k=2)
    assert result.selected == [(1, 1.0, "rerank"), (3, 1 / 3, "rerank")]


def test_rank_ties_are_stable() -> None:
    assert rank_candidates([(2, 0.2, "dense"), (1, 0.1, "bm25")], [0.8, 0.8]) == [
        (1, 0.8, "rerank"),
        (2, 0.8, "rerank"),
    ]


@pytest.mark.parametrize("scores", [[1.0], [float("nan"), 0.0], [0.0, float("inf")]])
def test_invalid_scores_fail_closed(scores) -> None:
    with pytest.raises(RerankError):
        rank_candidates([(1, 0.1, "dense"), (2, 0.1, "dense")], scores)


def test_invalid_policy_rejected() -> None:
    with pytest.raises(ValueError):
        choose([], "unknown")
    with pytest.raises(ValueError):
        choose([], k=0)


def test_empty_candidates() -> None:
    assert choose([]).selected == []


def test_document_balance_reserves_one_ranked_candidate_per_requested_document() -> None:
    first = UUID(int=100)
    second = UUID(int=101)
    values = [candidate(i) for i in range(1, 6)]
    values.append(candidate(6, document_id=second))

    result = choose(
        values,
        "legacy",
        k=3,
        balance_document_ids=frozenset({first, second}),
    )

    assert [row[0] for row in result.selected] == [1, 2, 6]


def test_document_balance_does_not_invent_missing_document_candidates() -> None:
    first = UUID(int=100)
    second = UUID(int=101)
    result = choose(
        [candidate(1), candidate(2)],
        "legacy",
        k=2,
        balance_document_ids=frozenset({first, second}),
    )
    assert [row[0] for row in result.selected] == [1, 2]


def test_document_balance_rejects_more_documents_than_slots() -> None:
    with pytest.raises(ValueError, match="one slot per document"):
        choose(
            [candidate(1)],
            k=1,
            balance_document_ids=frozenset({UUID(int=100), UUID(int=101)}),
        )


def test_document_balance_does_not_cross_document_hash_deduplicate() -> None:
    first = UUID(int=101)
    second = UUID(int=102)
    candidates = {
        1: candidate(1, document_id=first, content_hash="same"),
        2: candidate(2, document_id=second, content_hash="same"),
    }

    result = select_evidence(
        [(1, 2.0, "rerank"), (2, 1.0, "rerank")],
        candidates,
        top_k=2,
        balance_document_ids=frozenset({first, second}),
    )

    assert [row[0] for row in result.selected] == [1, 2]
