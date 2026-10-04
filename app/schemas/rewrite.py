from __future__ import annotations

import uuid
from typing import Self

from pydantic import Field, model_validator

from app.schemas.common import CamelModel


class StructuredRewrite(CamelModel):
    standalone_query: str = Field(min_length=1, max_length=4000)
    paper_hints: list[str] = Field(default_factory=list)
    dataset_hints: list[str] = Field(default_factory=list)
    method_hints: list[str] = Field(default_factory=list)
    metric_hints: list[str] = Field(default_factory=list)

    def retrieval_query(self) -> str:
        values = [
            self.standalone_query,
            *self.paper_hints,
            *self.dataset_hints,
            *self.method_hints,
            *self.metric_hints,
        ]
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if normalized and normalized.casefold() not in seen:
                unique.append(normalized)
                seen.add(normalized.casefold())
        return " ".join(unique)


class EvidenceRefinementPlan(CamelModel):
    """One bounded decision after the primary retrieval pass."""

    evidence_sufficient: bool
    subquery: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def subquery_matches_decision(self) -> Self:
        if self.evidence_sufficient and self.subquery is not None:
            raise ValueError("sufficient evidence must not include a subquery")
        if not self.evidence_sufficient and self.subquery is None:
            raise ValueError("insufficient evidence requires one subquery")
        if self.subquery is not None:
            normalized = " ".join(self.subquery.split())
            if not normalized:
                raise ValueError("subquery must not be blank")
            self.subquery = normalized
        return self


class DocumentRoute(CamelModel):
    """One paper-scoped retrieval query selected from an allowed catalog."""

    document_id: uuid.UUID
    subquery: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def normalize_subquery(self) -> Self:
        normalized = " ".join(self.subquery.split())
        if not normalized:
            raise ValueError("subquery must not be blank")
        self.subquery = normalized
        return self


class DocumentRoutingPlan(CamelModel):
    """Bounded, confidence-gated paper routing proposed by the LLM."""

    confidence: float = Field(ge=0.0, le=1.0)
    routes: list[DocumentRoute] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def document_ids_are_unique(self) -> Self:
        ids = [route.document_id for route in self.routes]
        if len(ids) != len(set(ids)):
            raise ValueError("document routes must use unique document_ids")
        return self
