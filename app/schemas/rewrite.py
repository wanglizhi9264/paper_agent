from __future__ import annotations

from pydantic import Field

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
