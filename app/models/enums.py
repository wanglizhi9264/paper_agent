from __future__ import annotations

import enum
from typing import Final


class DocumentStatus(str, enum.Enum):
    """Document lifecycle (spec §10)."""

    UPLOADED = "uploaded"
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"
    DELETING = "deleting"
    DELETED = "deleted"


class JobKind(str, enum.Enum):
    INGEST = "ingest"
    REINDEX = "reindex"
    DELETE_CLEANUP = "delete_cleanup"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStage(str, enum.Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    FINALIZING = "finalizing"


class DocumentVersionStatus(str, enum.Enum):
    BUILDING = "building"
    READY = "ready"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class IndexSnapshotStatus(str, enum.Enum):
    BUILDING = "building"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class ChunkKind(str, enum.Enum):
    TEXT = "text"
    TITLE = "title"
    TABLE = "table"
    CODE = "code"
    CHAPTER = "chapter"


class SessionScopeType(str, enum.Enum):
    ALL = "all"
    DOCUMENTS = "documents"
    COLLECTION = "collection"


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class MessageStatus(str, enum.Enum):
    COMPLETE = "complete"
    INTERRUPTED = "interrupted"


# Allowlist of media types (spec §17, §11). Extension validation also enforced
# at the API boundary; this is the authoritative allowlist used by loaders.
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset({"pdf", "docx", "md"})

ALLOWED_MEDIA_TYPES: Final[dict[str, str]] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown",
}
