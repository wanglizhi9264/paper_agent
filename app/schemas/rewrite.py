from __future__ import annotations

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
