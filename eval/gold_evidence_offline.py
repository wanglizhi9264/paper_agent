"""Audit every private benchmark anchor against pinned local PDF/IR artifacts.

This command is database-independent.  It emits stable chunk locators for
curation, never pretending that ``document_key:chunk-N`` is a runtime UUID.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pymupdf

from app.chunking.pipeline import chunk_document_ir
from app.document_ir.serialize import read_ir
from eval.gold_evidence import (
    EvidenceAnchor,
    EvidenceChunk,
    EvidenceResolution,
    MatchMethod,
    ResolutionStatus,
    apply_review_decision,
    immutable_qa_digest,
    normalize_evidence_text,
    resolve_evidence,
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_pages(path: Path) -> dict[int, str]:
    pymupdf_api = cast(Any, pymupdf)  # The installed PyMuPDF wheel has no type information.
    document = pymupdf_api.open(path)
    try:
        return {
            index + 1: document.load_page(index).get_text() for index in range(document.page_count)
        }
    finally:
        document.close()


def _question_status(resolutions: list[EvidenceResolution]) -> str:
    statuses = {resolution.status for resolution in resolutions}
    for status in (
        ResolutionStatus.UNRESOLVED,
        ResolutionStatus.PARSER_ISSUE,
        ResolutionStatus.MANUAL_REVIEW,
        ResolutionStatus.MULTI_CHUNK,
    ):
        if status in statuses:
            return status.value
    return ResolutionStatus.RESOLVED.value


def _load_review_decisions(path: Path | None) -> dict[tuple[str, int], dict[str, Any]]:
    if path is None:
        return {}
    payload = _load(path)
    if payload.get("schema_version") != "gold-review-decisions-v1":
        raise ValueError("review decisions must use gold-review-decisions-v1")
    decisions: dict[tuple[str, int], dict[str, Any]] = {}
    for value in payload.get("decisions", []):
        if not isinstance(value, dict):
            raise ValueError("each review decision must be an object")
        key = (str(value["question_id"]), int(value["evidence_index"]))
        if key in decisions:
            raise ValueError(f"duplicate review decision: {key}")
        if value.get("decision") != "source_verified":
            raise ValueError(f"{key}: only source_verified decisions can bind gold evidence")
        if not str(value.get("source_verified_text", "")).strip():
            raise ValueError(f"{key}: source_verified_text is required")
        hashes = value.get("approved_chunk_content_hashes", [])
        if not hashes or len(hashes) != len(set(hashes)):
            raise ValueError(f"{key}: approved chunk content hashes must be unique and non-empty")
        decisions[key] = value
    return decisions


def _document_ids(prior: dict[str, Any]) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for item in prior.get("dataset", []):
        keys = item.get("scope", {}).get("document_keys", [])
        ids = item.get("runtime_scope", {}).get("document_ids", [])
        if len(keys) == len(ids):
            for key, document_id in zip(keys, ids, strict=True):
                candidates.setdefault(str(key), set()).add(str(document_id))
    ambiguous = {key: values for key, values in candidates.items() if len(values) != 1}
    if ambiguous:
        raise ValueError(f"ambiguous runtime document mapping: {ambiguous}")
    return {key: next(iter(values)) for key, values in candidates.items()}


def _artifact_inputs(
    dataset: dict[str, Any], prior: dict[str, Any], storage: Path, snapshot_id: str
) -> tuple[dict[str, list[EvidenceChunk]], dict[tuple[str, int], str], dict[tuple[str, int], str]]:
    manifest = _load(storage / "indexes" / "versions" / snapshot_id / "manifest.json")
    version_by_document = manifest.get("document_versions", {})
    document_ids = _document_ids(prior)
    uploads = {_sha256(path): path for path in (storage / "uploads").glob("*.pdf")}
    chunks_by_document: dict[str, list[EvidenceChunk]] = {}
    parsed_pages: dict[tuple[str, int], str] = {}
    source_pages: dict[tuple[str, int], str] = {}

    for metadata in dataset.get("documents", []):
        key = str(metadata["document_key"])
        document_id = document_ids[key]
        version_id = str(version_by_document[document_id])
        version_dir = storage / "ir" / "versions" / version_id
        ir_paths = list(version_dir.glob("*/document_ir.json"))
        if len(ir_paths) != 1:
            raise ValueError(f"{key}: expected one pinned IR, found {len(ir_paths)}")
        ir = read_ir(ir_paths[0])
        if str(ir.document_id) != document_id:
            raise ValueError(f"{key}: IR document id does not match snapshot manifest")
        chunks: list[EvidenceChunk] = []
        for result in chunk_document_ir(ir):
            chunks.append(
                EvidenceChunk(
                    chunk_id=f"{key}:chunk-{result.chunk_index}",
                    document_key=key,
                    chunk_index=result.chunk_index,
                    raw_content=result.raw_content,
                    retrieval_content=result.retrieval_content,
                    content_hash=result.content_hash,
                    page_start=result.page_start,
                    page_end=result.page_end,
                    section_path=tuple(result.section_path),
                    kind=result.kind,
                    metadata=result.metadata,
                )
            )
        chunks_by_document[key] = chunks
        for page in ir.pages:
            values: list[str] = []
            for element in sorted(ir.elements, key=lambda value: value.reading_order):
                if any(span.physical_page == page.physical_page for span in element.provenance):
                    values.extend((element.raw_text, element.normalized_text))
            parsed_pages[(key, page.physical_page)] = "\n".join(values)
        source_path = uploads.get(str(metadata["sha256"]))
        if source_path is None:
            raise ValueError(f"{key}: source PDF with declared SHA-256 is unavailable")
        for physical_page, text in _source_pages(source_path).items():
            source_pages[(key, physical_page)] = text
    return chunks_by_document, parsed_pages, source_pages


def _excerpt(text: str, quote: str, limit: int = 700) -> str:
    normalized_quote = normalize_evidence_text(quote)
    normalized = normalize_evidence_text(text)
    position = normalized.find(normalized_quote)
    if position < 0:
        return text[:limit].strip()
    start = max(0, position - limit // 4)
    return text[start : start + limit].strip()


def _resolution_dict(
    resolution: EvidenceResolution, chunks: dict[str, EvidenceChunk], quote: str
) -> dict[str, Any]:
    value = resolution.to_dict()
    value["resolved_chunk_locators"] = value.pop("resolved_chunk_ids")
    candidates = cast(list[dict[str, Any]], value["candidates"])
    for candidate in candidates:
        locators = cast(list[str], candidate.pop("chunk_ids"))
        candidate["chunk_locators"] = locators
        candidate["chunk_content_hashes"] = [
            chunks[locator].content_hash for locator in locators if locator in chunks
        ]
        candidate["chunk_excerpts"] = [
            _excerpt(chunks[locator].raw_content, quote)
            for locator in locators
            if locator in chunks
        ]
    value["locator_warning"] = "stable offline locators; not runtime Chunk UUIDs"
    return value


def _markdown(payload: dict[str, Any], report: dict[str, Any]) -> str:
    lines = [
        "# Gold Evidence Benchmark 全量审计册",
        "",
        "> 本报告只依据原始 PDF、pinned Document IR 与确定性 chunker；未使用 Retriever 返回结果反向标注。",
        "> `document_key:chunk-N` 是离线稳定定位符，不是可直接计分的 Chunk UUID。",
        "",
        "## 汇总",
        "",
        f"- 总题数：{report['benchmark_total']}（可回答 {report['answerable_total']}，不可回答 {report['unanswerable_total']}）",
        f"- resolved：{report['resolved']}；multi_chunk：{report['multi_chunk']}；parser_issue：{report['parser_issue']}；manual_review：{report['manual_review']}；unresolved：{report['unresolved']}",
        f"- 经 PDF 原页与 pinned IR 人工核验的 evidence anchor：{report['manual_verified_anchors']}",
        f"- 人工核验前：resolved {report['pre_review_status']['resolved']}；parser_issue {report['pre_review_status']['parser_issue']}；manual_review {report['pre_review_status']['manual_review']}；unresolved {report['pre_review_status']['unresolved']}",
        f"- Snapshot：`{report['snapshot_id']}`",
        f"- 可冻结：**{'是' if report['freeze_permitted'] else '否'}**",
        "",
        "## 全部题目",
        "",
        "| ID | 类型 | 可回答 | Evidence 状态 | 题目 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in payload["dataset"]:
        question = str(item["question"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item['id']} | {item['question_type']} | {'是' if item['answerable'] else '否'} | "
            f"{item['evidence_resolution_status']} | {question} |"
        )
    for item in payload["dataset"]:
        lines.extend(("", f"## {item['id']} — {item['question']}", ""))
        lines.append(
            f"- 类型：`{item['question_type']}`；split：`{item['split']}`；可回答：`{item['answerable']}`"
        )
        lines.extend(("", "**参考答案**", "", str(item["reference_answer"] or "（不可回答）")))
        if not item.get("evidence"):
            lines.extend(("", "**证据要求**：不可回答题，不设置 gold evidence。"))
            continue
        for index, evidence in enumerate(item["evidence"], start=1):
            resolution = evidence["offline_resolution"]
            lines.extend(
                (
                    "",
                    f"### Evidence {index}",
                    "",
                    f"- 文档：`{evidence['document_key']}`；页：{evidence['page']}；章节：{' / '.join(evidence.get('section_path', [])) or '未标注'}",
                    f"- 状态：`{resolution['status']}`；方法：`{resolution['match_method']}`；原因：`{resolution['reason_code']}`",
                    "- Gold quote：",
                    "",
                    f"> {str(evidence['quote']).replace(chr(10), chr(10) + '> ')}",
                )
            )
            locators = resolution["resolved_chunk_locators"]
            if locators:
                lines.append(f"- 解析定位符：{', '.join(f'`{value}`' for value in locators)}")
            decision = evidence.get("review_decision")
            if decision:
                lines.extend(
                    (
                        "- 核验方式：PDF 原页视觉核验 + pinned IR content hash 校验",
                        f"- 核验日期：`{decision['reviewed_at']}`；核验者：`{decision['reviewed_by']}`",
                        "- PDF 原文核验文本：",
                        "",
                        f"> {str(decision['source_verified_text']).replace(chr(10), chr(10) + '> ')}",
                        f"- 核验说明：{decision['rationale']}",
                    )
                )
            for candidate_index, candidate in enumerate(resolution["candidates"], start=1):
                lines.extend(
                    (
                        "",
                        f"**候选 {candidate_index}**：{', '.join(f'`{value}`' for value in candidate['chunk_locators'])}；coverage={candidate['token_coverage']:.3f}；section_match={candidate['section_matches']}",
                    )
                )
                for excerpt in candidate["chunk_excerpts"]:
                    lines.extend(("", "```text", excerpt, "```"))
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    original = _load(args.dataset)
    prior = _load(args.prior_resolved)
    repaired = copy.deepcopy(original)
    review_decisions = _load_review_decisions(args.review_decisions)
    used_review_decisions: set[tuple[str, int]] = set()
    source_hashes = {
        str(value["document_key"]): str(value["sha256"]) for value in original.get("documents", [])
    }
    snapshot_ids = {
        str(item.get("snapshot_labels", {}).get("index_snapshot_id"))
        for item in prior.get("dataset", [])
        if item.get("snapshot_labels", {}).get("index_snapshot_id")
    }
    if len(snapshot_ids) != 1:
        raise ValueError("prior resolved dataset must pin exactly one snapshot")
    snapshot_id = next(iter(snapshot_ids))
    chunks_by_document, parsed_pages, source_pages = _artifact_inputs(
        original, prior, args.storage, snapshot_id
    )
    flat_chunks = {
        chunk.chunk_id: chunk for chunks in chunks_by_document.values() for chunk in chunks
    }
    counts: Counter[str] = Counter()
    automatic_counts: Counter[str] = Counter()
    answerable_total = 0
    manual_verified_anchors = 0
    reviewed_exceptions: list[dict[str, Any]] = []
    for item in repaired.get("dataset", []):
        if not item.get("answerable"):
            item["evidence_resolution_status"] = "not_applicable"
            continue
        answerable_total += 1
        resolutions: list[EvidenceResolution] = []
        automatic_resolutions: list[EvidenceResolution] = []
        for evidence_index, evidence in enumerate(item.get("evidence", [])):
            anchor = EvidenceAnchor(
                document_key=str(evidence["document_key"]),
                page=int(evidence["page"]),
                section_path=tuple(str(value) for value in evidence.get("section_path", [])),
                quote=str(evidence["quote"]),
                quote_hash=str(evidence["quote_hash"]) if evidence.get("quote_hash") else None,
                quote_hash_version=str(evidence.get("quote_hash_version", "legacy-v1")),
            )
            resolution = resolve_evidence(
                anchor,
                chunks_by_document.get(anchor.document_key, []),
                parsed_page_text=parsed_pages.get((anchor.document_key, anchor.page)),
                source_page_text=source_pages.get((anchor.document_key, anchor.page)),
            )
            automatic_resolution = resolution
            automatic_resolutions.append(automatic_resolution)
            decision_key = (str(item["id"]), evidence_index)
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
                    chunks=chunks_by_document.get(anchor.document_key, []),
                    source_pdf_sha256=source_hashes[anchor.document_key],
                    snapshot_id=snapshot_id,
                )
                evidence["review_decision"] = copy.deepcopy(decision)
                used_review_decisions.add(decision_key)
                if resolution.method is MatchMethod.MANUAL_VERIFIED:
                    manual_verified_anchors += 1
                reviewed_exceptions.append(
                    {
                        "question_id": item["id"],
                        "evidence_index": evidence_index,
                        "initial_status": automatic_resolution.status.value,
                        "initial_reason_code": automatic_resolution.reason_code,
                        "final_status": resolution.status.value,
                        "approved_chunk_content_hashes": decision["approved_chunk_content_hashes"],
                        "rationale": decision["rationale"],
                    }
                )
            evidence["offline_resolution"] = _resolution_dict(resolution, flat_chunks, anchor.quote)
            resolutions.append(resolution)
        status = _question_status(resolutions)
        automatic_counts[_question_status(automatic_resolutions)] += 1
        item["evidence_resolution_status"] = status
        counts[status] += 1
    unused_decisions = set(review_decisions) - used_review_decisions
    if unused_decisions:
        raise ValueError(f"unused review decisions: {sorted(unused_decisions)}")
    report = {
        "benchmark_total": len(repaired.get("dataset", [])),
        "answerable_total": answerable_total,
        "unanswerable_total": len(repaired.get("dataset", [])) - answerable_total,
        **{status.value: counts[status.value] for status in ResolutionStatus},
        "snapshot_id": snapshot_id,
        "question_answer_sha256": immutable_qa_digest(original),
        "manual_verified_anchors": manual_verified_anchors,
        "pre_review_status": {
            status.value: automatic_counts[status.value] for status in ResolutionStatus
        },
        "reviewed_exceptions": reviewed_exceptions,
        "freeze_permitted": counts[ResolutionStatus.PARSER_ISSUE.value]
        + counts[ResolutionStatus.MANUAL_REVIEW.value]
        + counts[ResolutionStatus.UNRESOLVED.value]
        == 0,
        "label_binding": "offline_locators_only_database_uuid_rebind_required",
    }
    repaired["benchmark_audit"] = report
    if immutable_qa_digest(repaired) != immutable_qa_digest(original):
        raise RuntimeError("question/answer content changed during audit")
    _write(args.output_dir / "benchmark_curated_all.json", repaired)
    _write(args.output_dir / "benchmark_resolved.json", repaired)
    _write(args.output_dir / "benchmark_curated_report.json", report)
    _write(args.output_dir / "benchmark_repair_report.json", report)
    unresolved = {
        "benchmark_audit": report,
        "dataset": [
            item
            for item in repaired.get("dataset", [])
            if item.get("evidence_resolution_status")
            not in {"resolved", "multi_chunk", "not_applicable"}
        ],
    }
    _write(args.output_dir / "benchmark_unresolved.json", unresolved)
    (args.output_dir / "benchmark_all_questions.md").write_text(
        _markdown(repaired, report), encoding="utf-8"
    )
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--prior-resolved", type=Path, required=True)
    parser.add_argument("--storage", type=Path, default=Path("storage"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--review-decisions", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    report = run(_parser().parse_args(argv))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["freeze_permitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
