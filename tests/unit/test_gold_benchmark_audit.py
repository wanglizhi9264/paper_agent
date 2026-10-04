from __future__ import annotations

import copy

from eval.gold_benchmark_audit import audit_benchmark, render_markdown, render_review_markdown
from eval.gold_evidence import evidence_quote_hash


def sample(
    case_id: str,
    split: str,
    *,
    page: int,
    chunk_id: str,
    quote: str,
) -> dict:
    return {
        "id": case_id,
        "split": split,
        "question_type": "fact_definition",
        "question": f"Question {case_id}?",
        "answerable": True,
        "reference_answer": "Answer.",
        "reference_answer_points": ["Answer"],
        "evidence_resolution_status": "resolved",
        "evidence": [
            {
                "document_key": "paper-a",
                "page": page,
                "section_path": ["Methods"],
                "quote": quote,
                "quote_hash": evidence_quote_hash(quote),
                "quote_hash_version": "unicode-v2",
                "resolution": {
                    "status": "resolved",
                    "resolved_chunk_ids": [chunk_id],
                },
            }
        ],
        "relevant_chunk_ids": [chunk_id],
        "required_citation_chunk_ids": [chunk_id],
    }


def benchmark() -> dict:
    return {
        "dataset_version": "test-v1",
        "dataset": [
            sample("dev-001", "dev", page=1, chunk_id="chunk-a", quote="alpha evidence"),
            sample("test-001", "test", page=2, chunk_id="chunk-b", quote="beta evidence"),
            {
                "id": "test-002",
                "split": "test",
                "question_type": "unanswerable",
                "question": "Missing fact?",
                "answerable": False,
                "reference_answer": None,
                "reference_answer_points": [],
                "evidence": [],
                "evidence_resolution_status": "not_applicable",
                "relevant_chunk_ids": [],
                "required_citation_chunk_ids": [],
            },
        ],
    }


def test_audit_accepts_resolved_leakage_free_benchmark_deterministically() -> None:
    payload = benchmark()
    first = audit_benchmark(
        payload,
        expected_total=3,
        expected_dev=1,
        expected_test=2,
        require_leakage_free=True,
    )
    second = audit_benchmark(
        copy.deepcopy(payload),
        expected_total=3,
        expected_dev=1,
        expected_test=2,
        require_leakage_free=True,
    )
    assert first == second
    assert first["passed"] is True
    assert first["leakage"]["cross_split_source_page_count"] == 0
    assert "Status: **PASS**" in render_markdown(first)
    review = render_review_markdown(payload, first)
    assert "### dev-001 - dev - fact_definition" in review
    assert "alpha evidence" in review
    assert "**Reference answer:** Unanswerable" in review


def test_audit_rejects_cross_split_page_anchor_and_chunk_leakage() -> None:
    payload = benchmark()
    test_item = payload["dataset"][1]
    dev_item = payload["dataset"][0]
    test_item["evidence"] = copy.deepcopy(dev_item["evidence"])
    test_item["relevant_chunk_ids"] = ["chunk-a"]
    test_item["required_citation_chunk_ids"] = ["chunk-a"]

    report = audit_benchmark(payload, require_leakage_free=True)

    assert report["passed"] is False
    assert report["leakage"]["cross_split_evidence_anchor_count"] == 1
    assert report["leakage"]["cross_split_source_page_count"] == 1
    assert report["leakage"]["cross_split_resolved_chunk_count"] == 1


def test_audit_rejects_stale_hash_unresolved_positive_and_labeled_negative() -> None:
    payload = benchmark()
    positive = payload["dataset"][0]
    positive["evidence"][0]["quote_hash"] = "stale"
    positive["evidence"][0]["resolution"]["status"] = "manual_review"
    positive["evidence_resolution_status"] = "manual_review"
    negative = payload["dataset"][2]
    negative["reference_answer"] = "Invented answer"
    negative["relevant_chunk_ids"] = ["chunk-z"]

    report = audit_benchmark(payload)

    assert report["passed"] is False
    assert any("stale evidence quote hash" in value for value in report["errors"])
    assert any("non-final evidence status" in value for value in report["errors"])
    assert any("unanswerable sample has a reference answer" in value for value in report["errors"])
    assert any(
        "unanswerable sample has positive chunk labels" in value for value in report["errors"]
    )


def test_audit_does_not_mutate_question_or_answer_truth() -> None:
    payload = benchmark()
    before = copy.deepcopy(payload)

    audit_benchmark(payload, require_leakage_free=True)

    assert payload == before
