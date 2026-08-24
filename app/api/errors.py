from __future__ import annotations

from collections.abc import Mapping
from typing import Final

HTTP_BAD_REQUEST: Final[int] = 400
HTTP_UNPROCESSABLE: Final[int] = 422
HTTP_NOT_FOUND: Final[int] = 404
HTTP_CONFLICT: Final[int] = 409
HTTP_UNSUPPORTED_MEDIA: Final[int] = 415
HTTP_PAYLOAD_TOO_LARGE: Final[int] = 413
HTTP_SERVICE_UNAVAILABLE: Final[int] = 503
HTTP_INTERNAL_ERROR: Final[int] = 500


class AppError(Exception):
    """Base error carrying a stable machine code and HTTP status."""

    code: str = "INTERNAL_ERROR"
    status: int = HTTP_INTERNAL_ERROR
    message: str = "Internal error."

    def __init__(
        self,
        *,
        code: str | None = None,
        message: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message or self.message)
        if code is not None:
            self.code = code
        if message is not None:
            self.message = message
        self.details: Mapping[str, object] = dict(details) if details else {}


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status = HTTP_NOT_FOUND
    message = "Resource was not found."


class ConflictError(AppError):
    code = "CONFLICT"
    status = HTTP_CONFLICT
    message = "Operation conflicts with current state."


class UnsupportedMediaTypeError(AppError):
    code = "UNSUPPORTED_MEDIA_TYPE"
    status = HTTP_UNSUPPORTED_MEDIA
    message = "Unsupported media type."


class PayloadTooLargeError(AppError):
    code = "PAYLOAD_TOO_LARGE"
    status = HTTP_PAYLOAD_TOO_LARGE
    message = "Payload too large."


class InvalidScopeError(AppError):
    code = "INVALID_SCOPE"
    status = HTTP_BAD_REQUEST
    message = "Invalid retrieval scope."


class IndexUnavailableError(AppError):
    code = "INDEX_UNAVAILABLE"
    status = HTTP_SERVICE_UNAVAILABLE
    message = "Retrieval index is not available."


class DependencyUnavailableError(AppError):
    code = "DEPENDENCY_UNAVAILABLE"
    status = HTTP_SERVICE_UNAVAILABLE
    message = "A required dependency is unavailable."
