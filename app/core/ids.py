from __future__ import annotations

import uuid
from typing import Annotated

from pydantic import BeforeValidator

UUIDv4 = Annotated[
    uuid.UUID,
    BeforeValidator(lambda v: v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))),
]


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def new_uuid_str() -> str:
    return str(uuid.uuid4())
