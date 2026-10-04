"""Bounded, auditable evidence refinement for the chat use case."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.core.config import Settings
from app.embedding.base import EmbeddingError, EmbeddingProvider
from app.llm.base import LLMError, LLMMessage, LLMProvider
from app.llm.prompts import build_document_routing_prompt, build_evidence_refinement_prompt
from app.models.collection import CollectionDocument
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.schemas.rewrite import (
    DocumentRoutingPlan,
    EvidenceRefinementPlan,
    StructuredRewrite,
)
from app.schemas.search import (
    RetrievalRouteOut,
    SearchRequest,
    SearchResponse,
    SearchResultOut,
    SearchScope,
)
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


@dataclass(frozen=True)
class RoutingDocument:
    document_id: uuid.UUID
    title: str


@dataclass(frozen=True)
class RoutingOutcome:
    plan: DocumentRoutingPlan | None
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


async def plan_document_routes(
    provider: LLMProvider,
    rewrite: StructuredRewrite,
    documents: Sequence[RoutingDocument],
    *,
    max_routes: int,
    min_confidence: float,
    max_tokens: int,
) -> RoutingOutcome:
    """Ask the LLM for bounded routes, then enforce the server-owned scope."""
    catalog = [
        {"document_id": str(document.document_id), "title": document.title}
        for document in documents
    ]
    prompt = build_document_routing_prompt(
        rewrite.model_dump_json(),
        json.dumps(catalog, ensure_ascii=False),
        max_routes,
    )
    try:
        response = await provider.generate(
            [LLMMessage(role="user", content=prompt)],
            temperature=0.0,
            max_tokens=max_tokens,
        )
        plan = DocumentRoutingPlan.model_validate(_json_payload(response.text))
    except (LLMError, json.JSONDecodeError, ValidationError):
        return RoutingOutcome(plan=None, degraded_reasons=["DOCUMENT_ROUTE_PLAN_FAILED"])

    allowed_ids = {document.document_id for document in documents}
    if len(plan.routes) > max_routes or any(
        route.document_id not in allowed_ids for route in plan.routes
    ):
        return RoutingOutcome(plan=None, degraded_reasons=["DOCUMENT_ROUTE_SCOPE_REJECTED"])
    if plan.confidence < min_confidence:
        return RoutingOutcome(plan=None, degraded_reasons=["DOCUMENT_ROUTE_LOW_CONFIDENCE"])
    return RoutingOutcome(plan=plan, degraded_reasons=[])


async def _routing_documents_for_scope(
    session: AsyncSession,
    scope: SearchScope,
) -> list[RoutingDocument]:
    statement = select(Document.id, Document.title, Document.filename).where(
        Document.status == DocumentStatus.READY
    )
    if scope.type == "documents":
        statement = statement.where(Document.id.in_(scope.document_ids))
    elif scope.type == "collection":
        statement = statement.join(
            CollectionDocument,
            CollectionDocument.document_id == Document.id,
        ).where(CollectionDocument.collection_id == scope.collection_id)
    statement = statement.order_by(Document.id)
    rows = (await session.execute(statement)).all()
    return [RoutingDocument(document_id=row.id, title=row.title or row.filename) for row in rows]


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
    preserve_document_ids: frozenset[uuid.UUID] = frozenset(),
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
    eligible: list[SearchResultOut] = []
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
        eligible.append(result.model_copy(update={"score": scores[chunk_id]}))
        if selection_strategy == "cell_coverage" and result.element_id is not None:
            covered_cells.setdefault(table_key, set()).update(cell_ids)
    selected = eligible[:top_k]
    if preserve_document_ids and len(preserve_document_ids) <= top_k:
        document_counts: dict[uuid.UUID, int] = {}
        for item in selected:
            document_counts[item.document_id] = document_counts.get(item.document_id, 0) + 1

        route_order = [
            item.document_id for item in eligible if item.document_id in preserve_document_ids
        ]
        for document_id in dict.fromkeys(route_order):
            if document_counts.get(document_id, 0) > 0:
                continue
            replacement = next(
                (item for item in eligible if item.document_id == document_id),
                None,
            )
            if replacement is None:
                continue
            replace_index = next(
                (
                    index
                    for index in range(len(selected) - 1, -1, -1)
                    if document_counts.get(selected[index].document_id, 0) > 1
                    or selected[index].document_id not in preserve_document_ids
                ),
                None,
            )
            if replace_index is None:
                continue
            removed = selected[replace_index]
            document_counts[removed.document_id] -= 1
            selected[replace_index] = replacement
            document_counts[replacement.document_id] = (
                document_counts.get(replacement.document_id, 0) + 1
            )

        eligible_order = {item.chunk_id: index for index, item in enumerate(eligible)}
        selected.sort(key=lambda item: eligible_order[item.chunk_id])
    return [item.model_copy(update={"rank": rank}) for rank, item in enumerate(selected, start=1)]


async def _gather_routed_evidence(
    session: AsyncSession,
    rewrite: StructuredRewrite,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    settings: Settings,
    primary: SearchResponse,
    runner: SearchRunner,
    documents: Sequence[RoutingDocument],
    *,
    original_query: str,
    top_k: int,
) -> SearchResponse:
    if len(documents) < 2:
        return primary
    primary_query = rewrite.retrieval_query()
    planned_routes = await plan_document_routes(
        llm_provider,
        rewrite,
        documents,
        max_routes=settings.chat_routing_max_documents,
        min_confidence=settings.chat_routing_min_confidence,
        max_tokens=settings.chat_routing_max_tokens,
    )
    if planned_routes.plan is None:
        return _with_degraded(primary, planned_routes.degraded_reasons)

    responses = [primary]
    successful_document_ids: set[uuid.UUID] = set()
    retrieval_queries = [primary_query]
    retrieval_routes: list[RetrievalRouteOut] = []
    degraded = list(primary.degraded_reasons)
    for route in planned_routes.plan.routes:
        route_scope = SearchScope(type="documents", document_ids=[route.document_id])
        try:
            routed = await runner(
                session,
                SearchRequest(query=route.subquery, scope=route_scope, top_k=top_k),
                embedding_provider,
                original_query=original_query,
                settings=settings,
            )
        except (AppError, EmbeddingError):
            if "DOCUMENT_ROUTE_SEARCH_FAILED" not in degraded:
                degraded.append("DOCUMENT_ROUTE_SEARCH_FAILED")
            continue
        responses.append(routed)
        successful_document_ids.add(route.document_id)
        retrieval_queries.append(route.subquery)
        retrieval_routes.append(
            RetrievalRouteOut(query=route.subquery, document_ids=[route.document_id])
        )
        degraded.extend(reason for reason in routed.degraded_reasons if reason not in degraded)

    if len(responses) == 1:
        return _with_degraded(primary, degraded)
    return SearchResponse(
        original_query=original_query,
        rewritten_query=primary_query,
        results=merge_search_responses(
            responses,
            top_k=top_k,
            selection_strategy=settings.retrieval_selection,
            preserve_document_ids=frozenset(successful_document_ids),
        ),
        degraded_reasons=degraded,
        retrieval_queries=retrieval_queries,
        retrieval_routes=retrieval_routes,
        retrieval_workflow=settings.chat_retrieval_workflow,
    )


async def _gather_refined_evidence(
    session: AsyncSession,
    rewrite: StructuredRewrite,
    scope: SearchScope,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider,
    settings: Settings,
    primary: SearchResponse,
    runner: SearchRunner,
    *,
    original_query: str,
    top_k: int,
) -> SearchResponse:
    primary_query = rewrite.retrieval_query()
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
        retrieval_workflow=settings.chat_retrieval_workflow,
    )


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
    routing_documents: Sequence[RoutingDocument] | None = None,
) -> SearchResponse:
    """Run the configured bounded workflow without widening the session scope."""
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
    if workflow == "routed_multi_search":
        documents = (
            list(routing_documents)
            if routing_documents is not None
            else await _routing_documents_for_scope(session, scope)
        )
        return await _gather_routed_evidence(
            session,
            rewrite,
            embedding_provider,
            llm_provider,
            settings,
            primary,
            runner,
            documents,
            original_query=original_query,
            top_k=top_k,
        )
    return await _gather_refined_evidence(
        session,
        rewrite,
        scope,
        embedding_provider,
        llm_provider,
        settings,
        primary,
        runner,
        original_query=original_query,
        top_k=top_k,
    )


def _with_degraded(response: SearchResponse, reasons: list[str]) -> SearchResponse:
    degraded = list(response.degraded_reasons)
    degraded.extend(reason for reason in reasons if reason not in degraded)
    return response.model_copy(update={"degraded_reasons": degraded})
