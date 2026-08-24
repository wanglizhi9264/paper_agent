from __future__ import annotations

import pytest

from app.api.errors import ConflictError, NotFoundError


def test_app_error_subclasses_carry_codes() -> None:
    assert NotFoundError().code == "NOT_FOUND"
    assert NotFoundError().status == 404
    assert ConflictError(code="DOCUMENT_BUSY").code == "DOCUMENT_BUSY"


def test_app_error_details_dict() -> None:
    err = NotFoundError(details={"resource": "document"})
    assert err.details == {"resource": "document"}


def test_app_error_raises() -> None:
    with pytest.raises(NotFoundError):
        raise NotFoundError()
