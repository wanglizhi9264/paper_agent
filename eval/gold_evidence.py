"""Conservative resolution of stable gold-evidence anchors to current chunks.

The gold truth is the source locator (document, page, section, quote), never a
retrieval result or an embedding-nearest chunk.  This module is deliberately
pure so the same resolver can be used against ORM rows, reconstructed chunks,
and deterministic unit-test fixtures.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from typing import Any

from app.document_ir.normalize import normalize_for_retrieval


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    MULTI_CHUNK = "multi_chunk"
    PARSER_ISSUE = "parser_issue"
    MANUAL_REVIEW = "manual_review"
    UNRESOLVED = "unresolved"


class MatchMethod(StrEnum):
    EXACT = "exact"
    NORMALIZED_EXACT = "normalized_exact"
    ADJACENT_CHUNKS = "adjacent_chunks"
    MANUAL_VERIFIED = "manual_verified"
    FUZZY_CANDIDATE = "fuzzy_candidate"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class EvidenceAnchor:
    document_key: str
    page: int
    section_path: tuple[str, ...]
    quote: str
    quote_hash: str | None = None
    quote_hash_version: str = "legacy-v1"


@dataclass(frozen=True, slots=True)
class EvidenceChunk:
    chunk_id: str
    document_key: str
    chunk_index: int
    raw_content: str
    retrieval_content: str
    content_hash: str
    page_start: int | None = None
    page_end: int | None = None
    section_path: tuple[str, ...] = ()
    kind: str = "text"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def indexed(self) -> bool:
        return not (self.kind == "table" and self.metadata.get("chunk_subtype") == "table_parent")


@dataclass(frozen=True, slots=True)
class CandidateMatch:
    chunk_ids: tuple[str, ...]
    method: MatchMethod
    token_coverage: float
    section_matches: bool


@dataclass(frozen=True, slots=True)
class EvidenceResolution:
    status: ResolutionStatus
    method: MatchMethod
    resolved_chunk_ids: tuple[str, ...]
    candidates: tuple[CandidateMatch, ...]
    reason_code: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "match_method": self.method.value,
            "resolved_chunk_ids": list(self.resolved_chunk_ids),
            "candidates": [
                {
                    "chunk_ids": list(candidate.chunk_ids),
                    "match_method": candidate.method.value,
                    "token_coverage": candidate.token_coverage,
                    "section_matches": candidate.section_matches,
                }
                for candidate in self.candidates
            ],
            "reason_code": self.reason_code,
        }


def normalize_evidence_text(text: str) -> str:
    """Apply the parser's unicode-v2 normalization and case-folding only."""
    return normalize_for_retrieval(text, allow_empty=True).text.casefold()


def normalize_evidence_alignment(text: str) -> str:
    """Normalize representation-only PDF/Markdown spacing for source alignment.

    This preserves words, numbers, operators, and punctuation.  It only removes
    Markdown table separators and whitespace introduced around punctuation or
    within hyphenated/math tokens by PDF extraction.
    """
    normalized = normalize_evidence_text(text)
    normalized = re.sub(r"[|`]+", " ", normalized)
    normalized = re.sub(r"\s*([,.;:()\[\]])\s*", r"\1", normalized)
    normalized = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def evidence_quote_hash(text: str, version: str = "unicode-v2") -> str:
    if version == "unicode-v2":
        normalized = normalize_evidence_text(text)
    elif version == "legacy-v1":
        normalized = normalize_evidence_text(text)
        normalized = re.sub(r"[-‐‑‒–—−]+", "", normalized)
        normalized = re.sub(r"[^\w%\.]+", " ", normalized, flags=re.UNICODE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
    else:
        raise ValueError(f"unsupported quote hash version: {version}")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_TOKEN_RE = re.compile(r"[^\W_]+(?:[.-][^\W_]+)*|[%±≤≥=+×÷∥εθΣμ]+", re.UNICODE)


def evidence_tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN_RE.findall(normalize_evidence_text(text)))


def page_overlaps(chunk: EvidenceChunk, page: int) -> bool:
    if chunk.page_start is None and chunk.page_end is None:
        return True
    start = chunk.page_start if chunk.page_start is not None else chunk.page_end
    end = chunk.page_end if chunk.page_end is not None else chunk.page_start
    return start is not None and end is not None and start <= page <= end


def section_matches(chunk: EvidenceChunk, expected: Sequence[str]) -> bool:
    if not expected:
        return True
    actual = [normalize_evidence_text(value) for value in chunk.section_path if value.strip()]
    if not actual:
        return False
    for value in expected:
        normalized = normalize_evidence_text(value)
        if not any(
            normalized == item or normalized in item or item in normalized for item in actual
        ):
            return False
    return True


def _prefer_section(
    chunks: Sequence[EvidenceChunk], expected: Sequence[str]
) -> list[EvidenceChunk]:
    matching = [chunk for chunk in chunks if section_matches(chunk, expected)]
    return matching or list(chunks)


def _prefer_source_specificity(chunks: Sequence[EvidenceChunk]) -> list[EvidenceChunk]:
    """Collapse deterministic duplicate representations, not semantic alternatives."""
    table_rows = [chunk for chunk in chunks if chunk.metadata.get("chunk_subtype") == "table_row"]
    if table_rows:
        return table_rows
    non_table_raw = [
        chunk for chunk in chunks if chunk.metadata.get("chunk_subtype") != "table_raw_text"
    ]
    return non_table_raw or list(chunks)


def _candidate(
    chunks: Sequence[EvidenceChunk],
    method: MatchMethod,
    quote_tokens: frozenset[str],
    expected_section: Sequence[str],
) -> CandidateMatch:
    combined = " ".join(chunk.raw_content for chunk in chunks)
    actual_tokens = evidence_tokens(combined)
    coverage = len(quote_tokens & actual_tokens) / len(quote_tokens) if quote_tokens else 0.0
    return CandidateMatch(
        chunk_ids=tuple(chunk.chunk_id for chunk in chunks),
        method=method,
        token_coverage=round(coverage, 6),
        section_matches=all(section_matches(chunk, expected_section) for chunk in chunks),
    )


def _single_resolution(
    chunks: Sequence[EvidenceChunk],
    method: MatchMethod,
    quote_tokens: frozenset[str],
    expected_section: Sequence[str],
) -> EvidenceResolution:
    candidates = tuple(
        _candidate([chunk], method, quote_tokens, expected_section) for chunk in chunks
    )
    if len(chunks) == 1:
        return EvidenceResolution(
            ResolutionStatus.RESOLVED,
            method,
            (chunks[0].chunk_id,),
            candidates,
            "UNIQUE_SOURCE_MATCH",
        )
    return EvidenceResolution(
        ResolutionStatus.MANUAL_REVIEW,
        method,
        (),
        candidates,
        "MULTIPLE_SOURCE_MATCHES",
    )


def resolve_evidence(  # noqa: PLR0911 - explicit fail-closed outcomes are the contract
    anchor: EvidenceAnchor,
    chunks: Iterable[EvidenceChunk],
    *,
    parsed_page_text: str | None = None,
    source_page_text: str | None = None,
    fuzzy_threshold: float = 0.8,
    max_adjacent_chunks: int = 3,
) -> EvidenceResolution:
    """Resolve one source anchor without promoting fuzzy candidates to gold.

    ``source_page_text`` should come directly from the original PDF text layer;
    ``parsed_page_text`` should come from the active Canonical IR.  They are
    diagnostics only and can never create a resolved label.
    """
    if anchor.page < 1:
        raise ValueError("evidence page must be 1-based")
    if not anchor.quote.strip():
        raise ValueError("evidence quote must not be empty")
    if not 0.0 <= fuzzy_threshold <= 1.0:
        raise ValueError("fuzzy_threshold must be within 0..1")
    if max_adjacent_chunks < 2:
        raise ValueError("max_adjacent_chunks must be at least 2")
    if (
        anchor.quote_hash is not None
        and evidence_quote_hash(anchor.quote, anchor.quote_hash_version) != anchor.quote_hash
    ):
        return EvidenceResolution(
            ResolutionStatus.MANUAL_REVIEW,
            MatchMethod.NONE,
            (),
            (),
            "QUOTE_HASH_MISMATCH",
        )

    document_chunks = sorted(
        (chunk for chunk in chunks if chunk.document_key == anchor.document_key),
        key=lambda chunk: (chunk.chunk_index, chunk.chunk_id),
    )
    scoped = [chunk for chunk in document_chunks if page_overlaps(chunk, anchor.page)]
    indexed = [chunk for chunk in scoped if chunk.indexed]
    quote_tokens = evidence_tokens(anchor.quote)
    quote_alignment = normalize_evidence_alignment(anchor.quote)

    direct = _prefer_section(
        [chunk for chunk in indexed if anchor.quote in chunk.raw_content], anchor.section_path
    )
    if direct:
        # A parser may emit the same source table as raw text, a table group,
        # and a row chunk. Include alignment-equivalent representations before
        # applying source-specificity so a literal hit in a fallback block does
        # not hide the canonical row representation.
        aligned = _prefer_section(
            [
                chunk
                for chunk in indexed
                if quote_alignment in normalize_evidence_alignment(chunk.raw_content)
            ],
            anchor.section_path,
        )
        preferred = _prefer_source_specificity(aligned)
        method = (
            MatchMethod.EXACT
            if all(anchor.quote in chunk.raw_content for chunk in preferred)
            else MatchMethod.NORMALIZED_EXACT
        )
        return _single_resolution(preferred, method, quote_tokens, anchor.section_path)

    normalized_raw = _prefer_source_specificity(
        _prefer_section(
            [
                chunk
                for chunk in indexed
                if quote_alignment in normalize_evidence_alignment(chunk.raw_content)
            ],
            anchor.section_path,
        )
    )
    if normalized_raw:
        return _single_resolution(
            normalized_raw, MatchMethod.NORMALIZED_EXACT, quote_tokens, anchor.section_path
        )

    # Structured table chunks may represent the same source faithfully but in
    # a header-bound order that differs from the PDF's visual reading order.
    normalized_retrieval = _prefer_source_specificity(
        _prefer_section(
            [
                chunk
                for chunk in indexed
                if chunk.metadata.get("chunk_subtype") in {"table_row", "table_group"}
                and quote_alignment in normalize_evidence_alignment(chunk.retrieval_content)
            ],
            anchor.section_path,
        )
    )
    if normalized_retrieval:
        return _single_resolution(
            normalized_retrieval,
            MatchMethod.NORMALIZED_EXACT,
            quote_tokens,
            anchor.section_path,
        )

    windows: list[list[EvidenceChunk]] = []
    for size in range(2, max_adjacent_chunks + 1):
        for index in range(len(indexed) - size + 1):
            window = indexed[index : index + size]
            if any(right.chunk_index != left.chunk_index + 1 for left, right in pairwise(window)):
                continue
            combined = normalize_evidence_alignment(" ".join(chunk.raw_content for chunk in window))
            if quote_alignment not in combined:
                continue
            if any(not (quote_tokens & evidence_tokens(chunk.raw_content)) for chunk in window):
                continue
            windows.append(window)
        if windows:
            break
    section_windows = [
        window
        for window in windows
        if all(section_matches(chunk, anchor.section_path) for chunk in window)
    ]
    windows = section_windows or windows
    if len(windows) == 1:
        window = windows[0]
        return EvidenceResolution(
            ResolutionStatus.MULTI_CHUNK,
            MatchMethod.ADJACENT_CHUNKS,
            tuple(chunk.chunk_id for chunk in window),
            (_candidate(window, MatchMethod.ADJACENT_CHUNKS, quote_tokens, anchor.section_path),),
            "UNIQUE_ADJACENT_WINDOW",
        )
    if windows:
        return EvidenceResolution(
            ResolutionStatus.MANUAL_REVIEW,
            MatchMethod.ADJACENT_CHUNKS,
            (),
            tuple(
                _candidate(window, MatchMethod.ADJACENT_CHUNKS, quote_tokens, anchor.section_path)
                for window in windows
            ),
            "MULTIPLE_ADJACENT_WINDOWS",
        )

    fuzzy: list[tuple[float, EvidenceChunk]] = []
    for chunk in indexed:
        actual = evidence_tokens(f"{chunk.raw_content} {chunk.retrieval_content}")
        coverage = len(quote_tokens & actual) / len(quote_tokens) if quote_tokens else 0.0
        if coverage >= fuzzy_threshold:
            fuzzy.append((coverage, chunk))
    fuzzy.sort(
        key=lambda item: (
            -item[0],
            not section_matches(item[1], anchor.section_path),
            item[1].chunk_index,
            item[1].chunk_id,
        )
    )
    fuzzy_candidates = tuple(
        _candidate([chunk], MatchMethod.FUZZY_CANDIDATE, quote_tokens, anchor.section_path)
        for _, chunk in fuzzy[:5]
    )

    source_contains = bool(
        source_page_text is not None
        and quote_alignment in normalize_evidence_alignment(source_page_text)
    )
    parsed_contains = bool(
        parsed_page_text is not None
        and quote_alignment in normalize_evidence_alignment(parsed_page_text)
    )
    if source_contains and not parsed_contains:
        return EvidenceResolution(
            ResolutionStatus.PARSER_ISSUE,
            MatchMethod.FUZZY_CANDIDATE if fuzzy_candidates else MatchMethod.NONE,
            (),
            fuzzy_candidates,
            "SOURCE_PRESENT_PARSER_OUTPUT_MISSING",
        )
    if fuzzy_candidates:
        return EvidenceResolution(
            ResolutionStatus.MANUAL_REVIEW,
            MatchMethod.FUZZY_CANDIDATE,
            (),
            fuzzy_candidates,
            "FUZZY_CANDIDATES_REQUIRE_REVIEW",
        )
    return EvidenceResolution(
        ResolutionStatus.UNRESOLVED,
        MatchMethod.NONE,
        (),
        (),
        "NO_SOURCE_MATCH",
    )


def apply_review_decision(
    *,
    decision: dict[str, Any],
    anchor: EvidenceAnchor,
    chunks: Sequence[EvidenceChunk],
    source_pdf_sha256: str,
    snapshot_id: str,
) -> EvidenceResolution:
    """Bind a source-verified decision by immutable source and content pins."""
    key = (decision["question_id"], decision["evidence_index"])
    if decision.get("decision") != "source_verified":
        raise ValueError(f"{key}: only source_verified decisions can bind gold evidence")
    if decision.get("document_key") != anchor.document_key:
        raise ValueError(f"{key}: review decision document does not match evidence")
    if decision.get("source_pdf_sha256") != source_pdf_sha256:
        raise ValueError(f"{key}: review decision is not pinned to the current source PDF")
    if decision.get("snapshot_id") != snapshot_id:
        raise ValueError(f"{key}: review decision is not pinned to the current snapshot")
    if int(decision.get("page", 0)) != anchor.page:
        raise ValueError(f"{key}: review decision page does not match evidence")
    current_quote_hash = evidence_quote_hash(anchor.quote, anchor.quote_hash_version)
    if decision.get("original_quote_hash") != current_quote_hash:
        raise ValueError(f"{key}: evidence quote changed after review")

    approved_hashes = tuple(str(value) for value in decision["approved_chunk_content_hashes"])
    if not approved_hashes or len(approved_hashes) != len(set(approved_hashes)):
        raise ValueError(f"{key}: approved chunk content hashes must be unique and non-empty")
    matched: list[EvidenceChunk] = []
    for content_hash in approved_hashes:
        matches = [
            chunk
            for chunk in chunks
            if chunk.content_hash == content_hash
            and chunk.document_key == anchor.document_key
            and page_overlaps(chunk, anchor.page)
        ]
        if len(matches) != 1:
            return EvidenceResolution(
                ResolutionStatus.MANUAL_REVIEW,
                MatchMethod.MANUAL_VERIFIED,
                (),
                (),
                "REVIEWED_CHUNK_CONTENT_CHANGED",
            )
        matched.append(matches[0])

    quote_tokens = evidence_tokens(anchor.quote)
    combined_tokens = evidence_tokens(" ".join(chunk.raw_content for chunk in matched))
    coverage = len(quote_tokens & combined_tokens) / len(quote_tokens) if quote_tokens else 0.0
    candidate = CandidateMatch(
        chunk_ids=tuple(chunk.chunk_id for chunk in matched),
        method=MatchMethod.MANUAL_VERIFIED,
        token_coverage=round(coverage, 6),
        section_matches=all(section_matches(chunk, anchor.section_path) for chunk in matched),
    )
    status = ResolutionStatus.MULTI_CHUNK if len(matched) > 1 else ResolutionStatus.RESOLVED
    return EvidenceResolution(
        status,
        MatchMethod.MANUAL_VERIFIED,
        candidate.chunk_ids,
        (candidate,),
        "SOURCE_AND_CHUNK_CONTENT_MANUALLY_VERIFIED",
    )


def resolution_group_hit(resolution: dict[str, Any], retrieved: Sequence[str], k: int) -> bool:
    """A multi-chunk evidence unit is hit only when every required chunk is retrieved."""
    if resolution.get("status") not in {
        ResolutionStatus.RESOLVED.value,
        ResolutionStatus.MULTI_CHUNK.value,
    }:
        return False
    required = {str(value) for value in resolution.get("resolved_chunk_ids", [])}
    return bool(required) and required.issubset(set(retrieved[:k]))


def immutable_qa_digest(payload: dict[str, Any]) -> str:
    values = [
        {
            "id": item.get("id"),
            "question": item.get("question"),
            "reference_answer": item.get("reference_answer"),
            "reference_answer_points": item.get("reference_answer_points"),
        }
        for item in payload.get("dataset", [])
    ]
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _question_status(resolutions: Sequence[EvidenceResolution]) -> str:
    statuses = {resolution.status for resolution in resolutions}
    if ResolutionStatus.UNRESOLVED in statuses:
        return ResolutionStatus.UNRESOLVED.value
    if ResolutionStatus.PARSER_ISSUE in statuses:
        return ResolutionStatus.PARSER_ISSUE.value
    if ResolutionStatus.MANUAL_REVIEW in statuses:
        return ResolutionStatus.MANUAL_REVIEW.value
    if ResolutionStatus.MULTI_CHUNK in statuses:
        return ResolutionStatus.MULTI_CHUNK.value
    return ResolutionStatus.RESOLVED.value


def repair_benchmark(
    payload: dict[str, Any],
    chunks_by_document: dict[str, Sequence[EvidenceChunk]],
    *,
    snapshot_id: str,
    parsed_pages: dict[tuple[str, int], str] | None = None,
    source_pages: dict[tuple[str, int], str] | None = None,
    prior_payload: dict[str, Any] | None = None,
    review_decisions: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a repaired copy plus a complete, non-mutating audit report."""
    repaired = copy.deepcopy(payload)
    before_digest = immutable_qa_digest(payload)
    parsed_pages = parsed_pages or {}
    source_pages = source_pages or {}
    prior_by_id = {str(item.get("id")): item for item in (prior_payload or {}).get("dataset", [])}
    review_decisions = review_decisions or {}
    used_review_decisions: set[tuple[str, int]] = set()
    source_hashes = {
        str(value["document_key"]): str(value.get("sha256", ""))
        for value in payload.get("documents", [])
    }
    all_current_ids = {chunk.chunk_id for chunks in chunks_by_document.values() for chunk in chunks}
    category_counts = {status.value: 0 for status in ResolutionStatus}
    exceptions: list[dict[str, Any]] = []
    stale_question_ids: list[str] = []
    resolved_questions = 0
    direct_resolved_questions = 0
    normalization_resolved_questions = 0
    manual_verified_anchors = 0

    for item in repaired.get("dataset", []):
        case_id = str(item.get("id"))
        if not item.get("answerable"):
            item["relevant_chunk_ids"] = []
            item["required_citation_chunk_ids"] = []
            item["snapshot_labels"] = {
                "index_snapshot_id": snapshot_id,
                "relevant_chunk_ids": [],
                "evidence_groups": [],
            }
            item["evidence_resolution_status"] = "not_applicable"
            continue

        resolutions: list[EvidenceResolution] = []
        evidence_groups: list[dict[str, Any]] = []
        for evidence_index, evidence in enumerate(item.get("evidence", [])):
            anchor = EvidenceAnchor(
                document_key=str(evidence["document_key"]),
                page=int(evidence["page"]),
                section_path=tuple(str(value) for value in evidence.get("section_path", [])),
                quote=str(evidence["quote"]),
                quote_hash=str(evidence["quote_hash"]) if evidence.get("quote_hash") else None,
                quote_hash_version=str(evidence.get("quote_hash_version") or "legacy-v1"),
            )
            resolution = resolve_evidence(
                anchor,
                chunks_by_document.get(anchor.document_key, ()),
                parsed_page_text=parsed_pages.get((anchor.document_key, anchor.page)),
                source_page_text=source_pages.get((anchor.document_key, anchor.page)),
            )
            decision_key = (case_id, evidence_index)
            decision = review_decisions.get(decision_key)
            if decision is not None:
                if resolution.status in {
                    ResolutionStatus.RESOLVED,
                    ResolutionStatus.MULTI_CHUNK,
                }:
                    raise ValueError(f"{decision_key}: review decision is no longer necessary")
                resolution = apply_review_decision(
                    decision=decision,
                    anchor=anchor,
                    chunks=chunks_by_document.get(anchor.document_key, ()),
                    source_pdf_sha256=source_hashes[anchor.document_key],
                    snapshot_id=snapshot_id,
                )
                evidence["review_decision"] = copy.deepcopy(decision)
                used_review_decisions.add(decision_key)
                if resolution.method is MatchMethod.MANUAL_VERIFIED:
                    manual_verified_anchors += 1
            resolutions.append(resolution)
            resolution_dict = resolution.to_dict()
            evidence["resolution"] = resolution_dict
            evidence_groups.append(
                {
                    "status": resolution.status.value,
                    "resolved_chunk_ids": list(resolution.resolved_chunk_ids),
                }
            )

        status = _question_status(resolutions)
        category_counts[status] += 1
        item["evidence_resolution_status"] = status
        fully_resolved = all(
            resolution.status in {ResolutionStatus.RESOLVED, ResolutionStatus.MULTI_CHUNK}
            for resolution in resolutions
        )
        resolved_ids = list(
            dict.fromkeys(
                chunk_id for resolution in resolutions for chunk_id in resolution.resolved_chunk_ids
            )
        )
        if fully_resolved:
            resolved_questions += 1
            if all(resolution.method is MatchMethod.EXACT for resolution in resolutions):
                direct_resolved_questions += 1
            if any(resolution.method is MatchMethod.NORMALIZED_EXACT for resolution in resolutions):
                normalization_resolved_questions += 1
        else:
            resolved_ids = []
            exceptions.append(
                {
                    "id": case_id,
                    "status": status,
                    "reasons": [
                        {
                            "evidence_index": index,
                            "document_key": item["evidence"][index]["document_key"],
                            "page": item["evidence"][index]["page"],
                            "status": resolution.status.value,
                            "reason_code": resolution.reason_code,
                            "candidate_chunk_ids": [
                                list(candidate.chunk_ids) for candidate in resolution.candidates
                            ],
                        }
                        for index, resolution in enumerate(resolutions)
                        if resolution.status
                        not in {ResolutionStatus.RESOLVED, ResolutionStatus.MULTI_CHUNK}
                    ],
                }
            )
        item["relevant_chunk_ids"] = resolved_ids
        item["required_citation_chunk_ids"] = resolved_ids
        item["snapshot_labels"] = {
            "index_snapshot_id": snapshot_id,
            "relevant_chunk_ids": resolved_ids,
            "evidence_groups": evidence_groups,
        }

        prior = prior_by_id.get(case_id, {})
        prior_snapshot = prior.get("snapshot_labels")
        prior_ids = (
            prior_snapshot.get("relevant_chunk_ids", [])
            if isinstance(prior_snapshot, dict)
            else prior.get("relevant_chunk_ids", [])
        )
        if any(str(chunk_id) not in all_current_ids for chunk_id in prior_ids):
            stale_question_ids.append(case_id)

    unused_review_decisions = set(review_decisions) - used_review_decisions
    if unused_review_decisions:
        raise ValueError(f"unused review decisions: {sorted(unused_review_decisions)}")

    after_digest = immutable_qa_digest(repaired)
    if before_digest != after_digest:
        raise RuntimeError("resolver modified benchmark question or answer truth")
    report = {
        "benchmark_total": len(repaired.get("dataset", [])),
        "answerable_total": sum(
            bool(item.get("answerable")) for item in repaired.get("dataset", [])
        ),
        "unanswerable_total": sum(
            not bool(item.get("answerable")) for item in repaired.get("dataset", [])
        ),
        "resolved": resolved_questions,
        "multi_chunk": category_counts[ResolutionStatus.MULTI_CHUNK.value],
        "parser_issue": category_counts[ResolutionStatus.PARSER_ISSUE.value],
        "manual_review": category_counts[ResolutionStatus.MANUAL_REVIEW.value],
        "unresolved": category_counts[ResolutionStatus.UNRESOLVED.value],
        "direct_resolve": direct_resolved_questions,
        "normalization_resolve": normalization_resolved_questions,
        "manual_verified_anchors": manual_verified_anchors,
        "stale_chunk_id": len(stale_question_ids),
        "stale_chunk_id_question_ids": stale_question_ids,
        "snapshot_id": snapshot_id,
        "question_answer_sha256": before_digest,
        "exceptions": exceptions,
    }
    return repaired, report
