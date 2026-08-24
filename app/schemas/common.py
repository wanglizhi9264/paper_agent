from __future__ import annotations

from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class CamelModel(BaseModel):
    """Base for API models: snake_case JSON, immutable outputs by default."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )


class Page(BaseModel, Generic[T]):
    """Cursor-paginated list (spec §8: default 20, max 100, stable sort by id)."""

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


def to_rfc3339(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
