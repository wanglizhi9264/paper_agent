from __future__ import annotations

from app.models.enums import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MEDIA_TYPES,
    ChunkKind,
    DocumentStatus,
    DocumentVersionStatus,
    IndexSnapshotStatus,
    JobKind,
    JobStage,
    JobStatus,
    MessageRole,
    MessageStatus,
    SessionScopeType,
)


def test_enum_values_match_spec() -> None:
    assert {s.value for s in DocumentStatus} == {
        "uploaded",
        "queued",
        "parsing",
        "chunking",
        "embedding",
        "indexing",
        "ready",
        "failed",
        "deleting",
        "deleted",
    }
    assert {k.value for k in JobKind} == {"ingest", "reindex", "delete_cleanup"}
    assert {s.value for s in JobStatus} == {"queued", "running", "succeeded", "failed", "cancelled"}
    assert {s.value for s in JobStage} == {
        "queued",
        "parsing",
        "chunking",
        "embedding",
        "indexing",
        "finalizing",
    }
    assert {s.value for s in DocumentVersionStatus} == {"building", "ready", "superseded", "failed"}
    assert {s.value for s in IndexSnapshotStatus} == {"building", "active", "superseded", "failed"}
    assert {k.value for k in ChunkKind} == {"text", "title", "table", "code", "chapter"}
    assert {s.value for s in SessionScopeType} == {"all", "documents", "collection"}
    assert {r.value for r in MessageRole} == {"user", "assistant", "system"}
    assert {s.value for s in MessageStatus} == {"complete", "interrupted"}


def test_extension_allowlist() -> None:
    assert frozenset({"pdf", "docx", "md"}) == ALLOWED_EXTENSIONS
    assert set(ALLOWED_MEDIA_TYPES) == {"pdf", "docx", "md"}


def test_enum_is_str_subclass() -> None:
    # Stored as constrained strings; enum members must serialize to their value.
    assert DocumentStatus.READY.value == "ready"
    assert isinstance(DocumentStatus.READY.value, str)
