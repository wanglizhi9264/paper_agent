from __future__ import annotations

import uuid
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import DependencyUnavailableError
from app.core.config import Settings, get_settings
from app.embedding.fake import FakeEmbeddingAdapter
from app.llm.base import LLMMessage, LLMResponse
from app.schemas.rewrite import EvidenceRefinementPlan, StructuredRewrite
from app.schemas.search import SearchResponse, SearchResultOut, SearchScope
from app.services.evidence_workflow import gather_chat_evidence, merge_search_responses


def _result(number: int, raw_content: str, *, rank: int) -> SearchResultOut:
    return SearchResultOut(
        chunk_id=uuid.UUID(int=number),
        document_id=uuid.UUID(int=100 + number),
        document_title=f"Paper {number}",
        section_path=["Results"],
        page_start=number,
        page_end=number,
        raw_content=raw_content,
        context_content=raw_content,
        score=1.0 / rank,
        rank=rank,
    )


def _response(query: str, results: list[SearchResultOut]) -> SearchResponse:
    return SearchResponse(
        original_query="original",
        rewritten_query=query,
        results=results,
        retrieval_queries=[query],
    )


class PlanProvider:
    def __init__(self, text: str, *, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[list[LLMMessage]] = []

    async def generate(self, messages: list[LLMMessage], **_kwargs: object) -> LLMResponse:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return LLMResponse(text=self.text)


class RecordingSearch:
    def __init__(
        self,
        responses: list[SearchResponse],
        *,
        second_error: Exception | None = None,
    ) -> None:
        self.responses = responses
        self.second_error = second_error
        self.calls: list[tuple[str, SearchScope, str | None]] = []

    async def __call__(
        self,
        _session: AsyncSession,
        request,
        _embedding_provider,
        *,
        original_query: str | None = None,
        settings: Settings | None = None,
    ) -> SearchResponse:
        assert settings is not None
        self.calls.append((request.query, request.scope, original_query))
        if len(self.calls) == 2 and self.second_error is not None:
            raise self.second_error
        return self.responses[len(self.calls) - 1]


def _settings(workflow: str) -> Settings:
    return get_settings().model_copy(update={"chat_retrieval_workflow": workflow})


def _rewrite() -> StructuredRewrite:
    return StructuredRewrite(
        standalone_query="Compare method A and method B on FID",
        method_hints=["method A", "method B"],
        metric_hints=["FID"],
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"evidence_sufficient": True, "subquery": "extra"},
        {"evidence_sufficient": False, "subquery": None},
        {"evidence_sufficient": False, "subquery": "   "},
    ],
)
def test_refinement_plan_requires_one_consistent_decision(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvidenceRefinementPlan.model_validate(payload)


@pytest.mark.asyncio
async def test_single_pass_never_calls_planner_or_second_search() -> None:
    search = RecordingSearch([_response("primary", [_result(1, "alpha", rank=1)])])
    planner = PlanProvider("not json")
    scope = SearchScope(type="all")

    response = await gather_chat_evidence(
        cast(AsyncSession, object()),
        _rewrite(),
        scope,
        FakeEmbeddingAdapter(),
        planner,  # type: ignore[arg-type]
        _settings("single_pass"),
        original_query="original",
        search_runner=search,
    )

    assert len(search.calls) == 1
    assert planner.calls == []
    assert response.retrieval_workflow == "single_pass"
    assert response.retrieval_queries == [_rewrite().retrieval_query()]


@pytest.mark.asyncio
async def test_sufficient_primary_evidence_stops_after_one_search_and_bounds_excerpt() -> None:
    long_text = "x" * 600 + "SHOULD_NOT_REACH_PROMPT"
    search = RecordingSearch([_response("primary", [_result(1, long_text, rank=1)])])
    planner = PlanProvider('{"evidence_sufficient":true,"subquery":null}')

    response = await gather_chat_evidence(
        cast(AsyncSession, object()),
        _rewrite(),
        SearchScope(type="all"),
        FakeEmbeddingAdapter(),
        planner,  # type: ignore[arg-type]
        _settings("bounded_refinement"),
        original_query="original",
        search_runner=search,
    )

    assert len(search.calls) == 1
    assert len(planner.calls) == 1
    assert "SHOULD_NOT_REACH_PROMPT" not in planner.calls[0][0].content
    assert response.retrieval_workflow == "bounded_refinement"


@pytest.mark.asyncio
async def test_invalid_planner_output_falls_back_with_stable_reason() -> None:
    search = RecordingSearch([_response("primary", [_result(1, "alpha", rank=1)])])

    response = await gather_chat_evidence(
        cast(AsyncSession, object()),
        _rewrite(),
        SearchScope(type="all"),
        FakeEmbeddingAdapter(),
        PlanProvider("not json"),  # type: ignore[arg-type]
        _settings("bounded_refinement"),
        original_query="original",
        search_runner=search,
    )

    assert len(search.calls) == 1
    assert response.degraded_reasons == ["EVIDENCE_PLAN_FAILED"]
    assert [item.chunk_id for item in response.results] == [uuid.UUID(int=1)]


@pytest.mark.asyncio
async def test_duplicate_refinement_query_stops_without_a_second_search() -> None:
    primary_query = _rewrite().retrieval_query()
    search = RecordingSearch([_response(primary_query, [_result(1, "alpha", rank=1)])])
    planner = PlanProvider(
        '{"evidence_sufficient":false,"subquery":' + __import__("json").dumps(primary_query) + "}"
    )

    response = await gather_chat_evidence(
        cast(AsyncSession, object()),
        _rewrite(),
        SearchScope(type="all"),
        FakeEmbeddingAdapter(),
        planner,  # type: ignore[arg-type]
        _settings("bounded_refinement"),
        original_query="original",
        search_runner=search,
    )

    assert len(search.calls) == 1
    assert response.degraded_reasons == []


@pytest.mark.asyncio
async def test_refinement_keeps_scope_and_merges_by_rank_only_rrf() -> None:
    primary = _response(
        "primary",
        [_result(1, "shared chunk", rank=1), _result(2, "primary-only", rank=2)],
    )
    refined = _response(
        "focused metric B",
        [_result(3, "refined-only", rank=1), _result(1, "shared chunk", rank=2)],
    )
    search = RecordingSearch([primary, refined])
    scope = SearchScope(type="documents", document_ids=[uuid.UUID(int=900)])
    planner = PlanProvider('{"evidence_sufficient":false,"subquery":"focused metric B"}')

    response = await gather_chat_evidence(
        cast(AsyncSession, object()),
        _rewrite(),
        scope,
        FakeEmbeddingAdapter(),
        planner,  # type: ignore[arg-type]
        _settings("bounded_refinement"),
        original_query="original",
        top_k=3,
        search_runner=search,
    )

    assert len(search.calls) == 2
    assert search.calls[0][1] == search.calls[1][1] == scope
    assert all(call[2] == "original" for call in search.calls)
    assert response.retrieval_queries == [_rewrite().retrieval_query(), "focused metric B"]
    assert [item.chunk_id for item in response.results] == [
        uuid.UUID(int=1),
        uuid.UUID(int=3),
        uuid.UUID(int=2),
    ]
    assert [item.rank for item in response.results] == [1, 2, 3]


@pytest.mark.asyncio
async def test_second_search_dependency_failure_preserves_primary_results() -> None:
    primary = _response("primary", [_result(1, "alpha", rank=1)])
    search = RecordingSearch(
        [primary],
        second_error=DependencyUnavailableError(message="embedding failed"),
    )

    response = await gather_chat_evidence(
        cast(AsyncSession, object()),
        _rewrite(),
        SearchScope(type="all"),
        FakeEmbeddingAdapter(),
        PlanProvider('{"evidence_sufficient":false,"subquery":"focused metric B"}'),  # type: ignore[arg-type]
        _settings("bounded_refinement"),
        original_query="original",
        search_runner=search,
    )

    assert len(search.calls) == 2
    assert response.degraded_reasons == ["REFINEMENT_SEARCH_FAILED"]
    assert [item.chunk_id for item in response.results] == [uuid.UUID(int=1)]


@pytest.mark.asyncio
async def test_unexpected_planner_bug_is_not_hidden_as_degradation() -> None:
    search = RecordingSearch([_response("primary", [_result(1, "alpha", rank=1)])])

    with pytest.raises(RuntimeError, match="planner bug"):
        await gather_chat_evidence(
            cast(AsyncSession, object()),
            _rewrite(),
            SearchScope(type="all"),
            FakeEmbeddingAdapter(),
            PlanProvider("", error=RuntimeError("planner bug")),  # type: ignore[arg-type]
            _settings("bounded_refinement"),
            original_query="original",
            search_runner=search,
        )


def test_merge_deduplicates_hash_after_rrf_ordering() -> None:
    primary = _response("q1", [_result(1, "same", rank=1), _result(2, "unique", rank=2)])
    refined = _response("q2", [_result(3, "same", rank=1)])

    merged = merge_search_responses([primary, refined], top_k=3, selection_strategy="legacy")

    assert len([item for item in merged if item.raw_content == "same"]) == 1
    assert len({item.chunk_id for item in merged}) == len(merged)


def test_cell_coverage_merge_preserves_cross_document_text_and_suppresses_covered_cells() -> None:
    document_id = uuid.UUID(int=500)
    table_id = uuid.UUID(int=600)
    cell_id = uuid.UUID(int=700)
    first = _result(1, "same", rank=1).model_copy(
        update={
            "document_id": document_id,
            "element_id": table_id,
            "cell_ids": [cell_id],
        }
    )
    covered_alias = _result(2, "different alias", rank=1).model_copy(
        update={
            "document_id": document_id,
            "element_id": table_id,
            "cell_ids": [cell_id],
        }
    )
    other_document = _result(3, "same", rank=2)

    merged = merge_search_responses(
        [_response("q1", [first]), _response("q2", [covered_alias, other_document])],
        top_k=3,
        selection_strategy="cell_coverage",
    )

    assert [item.chunk_id for item in merged] == [first.chunk_id, other_document.chunk_id]
