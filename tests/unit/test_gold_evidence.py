from __future__ import annotations

import copy

from eval.gold_evidence import (
    EvidenceAnchor,
    EvidenceChunk,
    MatchMethod,
    ResolutionStatus,
    apply_review_decision,
    evidence_quote_hash,
    normalize_evidence_alignment,
    normalize_evidence_text,
    repair_benchmark,
    resolution_group_hit,
    resolve_evidence,
)
from eval.gold_evidence_metrics import evaluate_gold_predictions


def chunk(
    chunk_id: str,
    text: str,
    *,
    document: str = "paper-a",
    index: int = 0,
    page: int = 4,
    section: tuple[str, ...] = ("Methods",),
    metadata: dict | None = None,
    content_hash: str | None = None,
) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=chunk_id,
        document_key=document,
        chunk_index=index,
        raw_content=text,
        retrieval_content=f"Paper\n{' > '.join(section)}\n{text}",
        content_hash=content_hash or f"hash-{chunk_id}",
        page_start=page,
        page_end=page,
        section_path=section,
        metadata=metadata or {},
    )


def test_alignment_normalization_handles_only_representation_spacing() -> None:
    assert normalize_evidence_alignment("| H + L | 17.58 | 52.84 |") == "h + l 17.58 52.84"
    assert normalize_evidence_alignment("one shot (i. e. , not autoregressive)") == (
        "one shot(i.e.,not autoregressive)"
    )
    assert normalize_evidence_alignment("2 × 10 -4") == "2 × 10-4"


def test_table_row_wins_over_duplicate_raw_and_group_representations() -> None:
    target = EvidenceAnchor("paper-a", 3, (), "Model 9.01 0.952")
    chunks = [
        chunk(
            "raw",
            "Model 9.01 0.952",
            index=1,
            page=3,
            section=(),
            metadata={"chunk_subtype": "table_raw_text"},
        ),
        chunk(
            "row",
            "| Model | 9.01 | 0.952 |",
            index=2,
            page=3,
            section=(),
            metadata={"chunk_subtype": "table_row"},
        ),
        chunk(
            "group",
            "Model 9.01 0.952",
            index=3,
            page=3,
            section=(),
            metadata={"chunk_subtype": "table_group"},
        ),
    ]
    result = resolve_evidence(target, chunks)
    assert result.status is ResolutionStatus.RESOLVED
    assert result.resolved_chunk_ids == ("row",)


def test_review_decision_rebinds_by_content_hash_after_chunk_id_change() -> None:
    quote = "where εθ predicts ε from xt"
    target = EvidenceAnchor(
        "paper-a",
        4,
        ("Methods",),
        quote,
        evidence_quote_hash(quote),
        "unicode-v2",
    )
    decision = {
        "question_id": "eval-001",
        "evidence_index": 0,
        "decision": "source_verified",
        "document_key": "paper-a",
        "page": 4,
        "source_pdf_sha256": "pdf-sha",
        "snapshot_id": "snapshot-a",
        "original_quote_hash": evidence_quote_hash(quote),
        "approved_chunk_content_hashes": ["stable-content"],
    }
    current = chunk(
        "new-chunk-id",
        "where ε θ predicts ε from x t",
        content_hash="stable-content",
    )

    result = apply_review_decision(
        decision=decision,
        anchor=target,
        chunks=[current],
        source_pdf_sha256="pdf-sha",
        snapshot_id="snapshot-a",
    )

    assert result.status is ResolutionStatus.RESOLVED
    assert result.method is MatchMethod.MANUAL_VERIFIED
    assert result.resolved_chunk_ids == ("new-chunk-id",)


def test_review_decision_fails_closed_when_chunk_content_changes() -> None:
    quote = "verified source text"
    target = EvidenceAnchor(
        "paper-a",
        4,
        ("Methods",),
        quote,
        evidence_quote_hash(quote),
        "unicode-v2",
    )
    decision = {
        "question_id": "eval-001",
        "evidence_index": 0,
        "decision": "source_verified",
        "document_key": "paper-a",
        "page": 4,
        "source_pdf_sha256": "pdf-sha",
        "snapshot_id": "snapshot-a",
        "original_quote_hash": evidence_quote_hash(quote),
        "approved_chunk_content_hashes": ["reviewed-content"],
    }

    result = apply_review_decision(
        decision=decision,
        anchor=target,
        chunks=[chunk("changed", quote, content_hash="changed-content")],
        source_pdf_sha256="pdf-sha",
        snapshot_id="snapshot-a",
    )

    assert result.status is ResolutionStatus.MANUAL_REVIEW
    assert result.resolved_chunk_ids == ()
    assert result.reason_code == "REVIEWED_CHUNK_CONTENT_CHANGED"


def anchor(text: str, *, document: str = "paper-a") -> EvidenceAnchor:
    return EvidenceAnchor(
        document_key=document,
        page=4,
        section_path=("Methods",),
        quote=text,
        quote_hash=evidence_quote_hash(text),
        quote_hash_version="unicode-v2",
    )


def dataset(text: str) -> dict:
    return {
        "documents": [{"document_key": "paper-a"}],
        "dataset": [
            {
                "id": "eval-001",
                "question": "Original question?",
                "answerable": True,
                "reference_answer": "Original answer.",
                "reference_answer_points": ["Original"],
                "scope": {"type": "documents", "document_keys": ["paper-a"]},
                "evidence": [
                    {
                        "document_key": "paper-a",
                        "page": 4,
                        "section_path": ["Methods"],
                        "quote": text,
                        "quote_hash": evidence_quote_hash(text),
                        "quote_hash_version": "unicode-v2",
                    }
                ],
            }
        ],
    }


def test_normalization_is_deterministic_and_preserves_math_semantics() -> None:
    raw = "ﬁt\u00a0ε − θ = 9.46 ± 0.11\r\ninter-\nnational"
    expected = "fit ε - θ = 9.46 ± 0.11 international"
    assert normalize_evidence_text(raw) == expected
    assert normalize_evidence_text(raw) == normalize_evidence_text(raw)


def test_resolution_is_deterministic_and_document_scoped() -> None:
    target = chunk("a", "The reported score is 9.46 ± 0.11.")
    other = chunk("b", "The reported score is 9.46 ± 0.11.", document="paper-b", index=1)
    evidence = anchor("score is 9.46 ± 0.11")
    first = resolve_evidence(evidence, [other, target])
    second = resolve_evidence(evidence, [target, other])
    assert first == second
    assert first.status is ResolutionStatus.RESOLVED
    assert first.resolved_chunk_ids == ("a",)


def test_multi_chunk_requires_the_unique_adjacent_window() -> None:
    chunks = [
        chunk("a", "The method reduces", index=1),
        chunk("b", "training time by half.", index=2),
    ]
    result = resolve_evidence(anchor("method reduces training time"), chunks)
    assert result.status is ResolutionStatus.MULTI_CHUNK
    assert result.method is MatchMethod.ADJACENT_CHUNKS
    assert result.resolved_chunk_ids == ("a", "b")
    resolution = result.to_dict()
    assert resolution_group_hit(resolution, ["a"], 10) is False
    assert resolution_group_hit(resolution, ["b", "a"], 10) is True


def test_parser_issue_is_not_marked_resolved() -> None:
    result = resolve_evidence(
        anchor("gold phrase"),
        [chunk("a", "parser omitted the phrase")],
        parsed_page_text="parser omitted the phrase",
        source_page_text="The PDF contains the gold phrase.",
    )
    assert result.status is ResolutionStatus.PARSER_ISSUE
    assert result.resolved_chunk_ids == ()


def test_ambiguous_exact_evidence_is_not_auto_selected() -> None:
    result = resolve_evidence(
        anchor("same evidence"),
        [chunk("a", "same evidence", index=1), chunk("b", "same evidence", index=2)],
    )
    assert result.status is ResolutionStatus.MANUAL_REVIEW
    assert result.reason_code == "MULTIPLE_SOURCE_MATCHES"
    assert result.resolved_chunk_ids == ()


def test_chunk_id_change_re_resolves_from_stable_evidence() -> None:
    evidence = anchor("stable quote")
    old = resolve_evidence(evidence, [chunk("old-id", "stable quote")])
    new = resolve_evidence(evidence, [chunk("new-id", "stable quote")])
    assert old.resolved_chunk_ids == ("old-id",)
    assert new.resolved_chunk_ids == ("new-id",)


def test_repair_never_modifies_question_answer_or_gold_quote() -> None:
    source = dataset("stable quote")
    original = copy.deepcopy(source)
    repaired, report = repair_benchmark(
        source,
        {"paper-a": [chunk("current-id", "stable quote")]},
        snapshot_id="snapshot-2",
        prior_payload={
            "dataset": [
                {
                    "id": "eval-001",
                    "snapshot_labels": {"relevant_chunk_ids": ["old-id"]},
                }
            ]
        },
    )
    assert source == original
    assert repaired["dataset"][0]["question"] == "Original question?"
    assert repaired["dataset"][0]["reference_answer"] == "Original answer."
    assert repaired["dataset"][0]["evidence"][0]["quote"] == "stable quote"
    assert repaired["dataset"][0]["relevant_chunk_ids"] == ["current-id"]
    assert report["stale_chunk_id"] == 1


def test_repair_uses_source_verified_decision_for_runtime_uuid_binding() -> None:
    source = dataset("where εθ predicts ε from xt")
    source["documents"][0]["sha256"] = "pdf-sha"
    current = chunk(
        "runtime-uuid",
        "where ε θ predicts ε from x t",
        content_hash="reviewed-content",
    )
    decision = {
        "question_id": "eval-001",
        "evidence_index": 0,
        "decision": "source_verified",
        "document_key": "paper-a",
        "page": 4,
        "source_pdf_sha256": "pdf-sha",
        "snapshot_id": "snapshot-2",
        "original_quote_hash": source["dataset"][0]["evidence"][0]["quote_hash"],
        "approved_chunk_content_hashes": ["reviewed-content"],
    }

    repaired, report = repair_benchmark(
        source,
        {"paper-a": [current]},
        snapshot_id="snapshot-2",
        review_decisions={("eval-001", 0): decision},
    )

    item = repaired["dataset"][0]
    assert item["relevant_chunk_ids"] == ["runtime-uuid"]
    assert item["snapshot_labels"]["evidence_groups"] == [
        {"status": "resolved", "resolved_chunk_ids": ["runtime-uuid"]}
    ]
    assert report["manual_verified_anchors"] == 1
    assert report["exceptions"] == []


def test_metrics_score_multi_chunk_as_one_strict_evidence_unit() -> None:
    items = [
        {
            "id": "eval-001",
            "answerable": True,
            "snapshot_labels": {
                "evidence_groups": [
                    {"status": "multi_chunk", "resolved_chunk_ids": ["a", "b"]},
                    {"status": "resolved", "resolved_chunk_ids": ["c"]},
                ]
            },
        }
    ]
    partial = evaluate_gold_predictions(
        items,
        [
            {
                "id": "eval-001",
                "retrieved_chunk_ids": ["a", "c"],
                "predicted_citation_chunk_ids": ["a", "c"],
            }
        ],
    )
    assert partial.recall_at_10 == 0.5
    assert partial.citation_recall == 0.5
    complete = evaluate_gold_predictions(
        items,
        [
            {
                "id": "eval-001",
                "retrieved_chunk_ids": ["a", "b", "c"],
                "predicted_citation_chunk_ids": ["a", "b", "c"],
            }
        ],
    )
    assert complete.recall_at_10 == 1.0
    assert complete.citation_recall == 1.0
