"""Bounded, auditable evidence refinement for the chat use case."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import Settings
from app.embedding.base import EmbeddingError, EmbeddingProvider
from app.llm.base import LLMError, LLMMessage, LLMProvider
from app.llm.prompts import build_evidence_refinement_prompt
from app.schemas.rewrite import EvidenceRefinementPlan, StructuredRewrite
from app.schemas.search import SearchRequest, SearchResponse, SearchResultOut, SearchScope
from app.services.retrieval import search_corpus


class SearchRunner(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        request: SearchRequest,
        embedding_provider: EmbeddingProvider,
        *,
        original_query: str | None = None,
        settings: Settings | None = None,
    ) -> SearchResponse: ...


_RRF_K = 60
_MAX_EXCERPTS = 6
_MAX_EXCERPT_CHARS = 600


@dataclass(frozen=True)
class RefinementOutcome:
    plan: EvidenceRefinementPlan | None
    degraded_reasons: list[str]


def _json_payload(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
    return json.loads(stripped)


def _evidence_excerpts(response: SearchResponse) -> str:
    if not response.results:
        return "(no candidates retrieved)"
    blocks: list[str] = []
    for index, result in enumerate(response.results[:_MAX_EXCERPTS], start=1):
        section = " > ".join(result.section_path) or "unknown"
        content = result.raw_content[:_MAX_EXCERPT_CHARS]
        blocks.append(
            f"[Candidate {index}]\n"
            f"Document: {result.document_title}\n"
            f"Section: {section}\n"
            f"Page: {result.page_start or 'unknown'}\n"
            f"Excerpt: {content}"
        )
    return "\n\n".join(blocks)


async def plan_evidence_refinement(
    provider: LLMProvider,
    rewrite: StructuredRewrite,
    primary: SearchResponse,
    *,
    max_tokens: int = 600,
) -> RefinementOutcome:
    prompt = build_evidence_refinement_prompt(
        rewrite.model_dump_json(),
        rewrite.retrieval_query(),
        _evidence_excerpts(primary),
    )
    try:
        response = await provider.generate(
            [LLMMessage(role="user", content=prompt)],
            temperature=0.0,
            max_tokens=max_tokens,
        )
        plan = EvidenceRefinementPlan.model_validate(_json_payload(response.text))
    except (LLMError, json.JSONDecodeError, ValidationError):
        return RefinementOutcome(plan=None, degraded_reasons=["EVIDENCE_PLAN_FAILED"])
    return RefinementOutcome(plan=plan, degraded_reasons=[])


def _dedup_key(result: SearchResultOut, strategy: str) -> tuple[str, ...]:
    digest = hashlib.sha256(result.raw_content.encode("utf-8")).hexdigest()
    if strategy == "cell_coverage":
        return (str(result.document_id), str(result.element_id or ""), digest)
    return (digest,)


def merge_search_responses(
    responses: list[SearchResponse],
    *,
    top_k: int,
    selection_strategy: str,
) -> list[SearchResultOut]:
    """Fuse per-query ranks without mixing model-specific raw scores."""
    if not responses or top_k < 1:
        raise ValueError("At least one response and a positive top_k are required")
    if selection_strategy not in {"legacy", "cell_coverage"}:
        raise ValueError("Invalid evidence selection policy")

    representatives: dict[str, SearchResultOut] = {}
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    first_query: dict[str, int] = {}
    for query_index, response in enumerate(responses):
        for rank, result in enumerate(response.results, start=1):
            key = str(result.chunk_id)
            representatives.setdefault(key, result)
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            best_rank[key] = min(best_rank.get(key, rank), rank)
            first_query.setdefault(key, query_index)

    ordered_ids = sorted(
        scores,
        key=lambda chunk_id: (
            -scores[chunk_id],
            best_rank[chunk_id],
            first_query[chunk_id],
            chunk_id,
        ),
    )
    selected: list[SearchResultOut] = []
    seen_hashes: set[tuple[str, ...]] = set()
    covered_cells: dict[tuple[str, str], set[str]] = {}
    for chunk_id in ordered_ids:
        result = representatives[chunk_id]
        hash_key = _dedup_key(result, selection_strategy)
        if hash_key in seen_hashes:
            continue
        table_key = (str(result.document_id), str(result.element_id or ""))
        cell_ids = {str(cell_id) for cell_id in result.cell_ids}
        if (
            selection_strategy == "cell_coverage"
            and result.element_id is not None
            and cell_ids
            and cell_ids.issubset(covered_cells.get(table_key, set()))
        ):
            continue
        seen_hashes.add(hash_key)
        selected.append(
            result.model_copy(update={"score": scores[chunk_id], "rank": len(selected) + 1})
        )
        if selection_strategy == "cell_coverage" and result.element_id is not None:
            covered_cells.setdefault(table_key, set()).update(cell_ids)
        if len(selected) == top_k:
            break
    return selected


async def gather_chat_evidence(
    session: AsyncSession,
    rewrite: StructuredRewrite,
    scope: SearchScope,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    settings: Settings,
    *,
    original_query: str,
    top_k: int = 8,
    search_runner: SearchRunner | None = None,
) -> SearchResponse:
    """Run one primary search and, when enabled, at most one refinement search."""
    runner = search_runner or search_corpus
    primary_query = rewrite.retrieval_query()
    primary = await runner(
        session,
        SearchRequest(query=primary_query, scope=scope, top_k=top_k),
        embedding_provider,
        original_query=original_query,
        settings=settings,
    )
    workflow = settings.chat_retrieval_workflow
    primary = primary.model_copy(
        update={"retrieval_queries": [primary_query], "retrieval_workflow": workflow}
    )
    if workflow == "single_pass":
        return primary

    planned = await plan_evidence_refinement(
        llm_provider,
        rewrite,
        primary,
        max_tokens=settings.chat_refinement_max_tokens,
    )
    if planned.plan is None:
        return _with_degraded(primary, planned.degraded_reasons)
    subquery = planned.plan.subquery
    if planned.plan.evidence_sufficient or subquery is None:
        return primary
    if subquery.casefold() == primary_query.casefold():
        return primary

    try:
        refined = await runner(
            session,
            SearchRequest(query=subquery, scope=scope, top_k=top_k),
            embedding_provider,
            original_query=original_query,
            settings=settings,
        )
    except (AppError, EmbeddingError):
        return _with_degraded(primary, ["REFINEMENT_SEARCH_FAILED"])

    degraded = list(primary.degraded_reasons)
    degraded.extend(reason for reason in refined.degraded_reasons if reason not in degraded)
    return SearchResponse(
        original_query=original_query,
        rewritten_query=primary_query,
        results=merge_search_responses(
            [primary, refined],
            top_k=top_k,
            selection_strategy=settings.retrieval_selection,
        ),
        degraded_reasons=degraded,
        retrieval_queries=[primary_query, subquery],
        retrieval_workflow=workflow,
    )


def _with_degraded(response: SearchResponse, reasons: list[str]) -> SearchResponse:
    degraded = list(response.degraded_reasons)
    degraded.extend(reason for reason in reasons if reason not in degraded)
    return response.model_copy(update={"degraded_reasons": degraded})
