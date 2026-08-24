from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

from app.core.ids import new_uuid_str
from app.core.logging import bind_request_context, clear_request_context


def _get_request_id(request: Request) -> str:
    header_id = request.headers.get("x-request-id")
    if header_id and header_id.isascii() and len(header_id) <= 64:
        return header_id
    return new_uuid_str()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request_id, bind it to structured log context, and echo it back."""

    async def dispatch(
        self, request: StarletteRequest, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = _get_request_id(request)
        request.state.request_id = request_id
        bind_request_context(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers["x-request-id"] = request_id
        return response


async def request_id_dep(request: Request) -> AsyncIterator[str]:
    yield getattr(request.state, "request_id", new_uuid_str())


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("invalid uuid format") from exc
