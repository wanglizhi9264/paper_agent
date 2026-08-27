"""Startup and consistency validation for active IndexSnapshot (spec §13.3).

On application startup, validates that the active FAISS index, its manifest,
and the database's DocumentVersion mappings are mutually consistent.
If validation fails, health returns ``degraded`` and search returns
``INDEX_UNAVAILABLE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.index.snapshot import SnapshotManifest, load_manifest, validate_manifest
from app.models.chunk import DocumentVersion
from app.models.enums import DocumentStatus, DocumentVersionStatus, IndexSnapshotStatus
from app.models.index_snapshot import IndexSnapshot, SystemState

logger = get_logger(__name__)


@dataclass
class IndexHealth:
    """Result of index consistency check (spec §13.3)."""

    healthy: bool
    snapshot_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    document_count: int = 0
    chunk_count: int = 0

    @property
    def status_label(self) -> str:
        return "ready" if self.healthy else "degraded"


async def check_index_health(session: AsyncSession) -> IndexHealth:
    """Validate the active IndexSnapshot at startup (spec §13.3)."""
    sys_state = await session.get(SystemState, 1)
    if sys_state is None or sys_state.active_index_snapshot_id is None:
        return IndexHealth(healthy=True, error_code="NOT_INITIALIZED")

    snapshot = await session.get(IndexSnapshot, sys_state.active_index_snapshot_id)
    if snapshot is None:
        return IndexHealth(
            healthy=False,
            error="active snapshot not found in DB",
            error_code="SNAPSHOT_MISSING",
        )

    if snapshot.status != "active":
        return IndexHealth(
            healthy=False,
            snapshot_id=str(snapshot.id),
            error=f"snapshot status is {snapshot.status}, expected active",
            error_code="SNAPSHOT_NOT_ACTIVE",
        )

    faiss_path = Path(snapshot.faiss_path) if snapshot.faiss_path else None
    if faiss_path is None or not faiss_path.exists():
        return IndexHealth(
            healthy=False,
            snapshot_id=str(snapshot.id),
            error=f"FAISS file missing: {faiss_path}",
            error_code="FAISS_FILE_MISSING",
        )

    # Load manifest from DB.
    manifest = SnapshotManifest.from_dict(snapshot.manifest or {})
    try:
        manifest_path = faiss_path.parent / "manifest.json"
        if manifest_path.exists():
            manifest = load_manifest(manifest_path)
    except Exception as exc:
        logger.warning("manifest_load_failed", error=str(exc))

    # Build DB document_versions map for validation.
    db_versions: dict[str, str] = {}
    active_docs_q = await session.execute(
        select(DocumentVersion).where(
            DocumentVersion.status == DocumentVersionStatus.READY,
        )
    )
    for ver in active_docs_q.scalars().all():
        db_versions[str(ver.document_id)] = str(ver.id)

    try:
        validate_manifest(
            manifest,
            faiss_path=faiss_path,
            expected_embedding_signature=snapshot.embedding_signature,
            expected_dimension=manifest.embedding.get("dimension"),
            db_document_versions=db_versions,
        )
    except Exception as exc:
        return IndexHealth(
            healthy=False,
            snapshot_id=str(snapshot.id),
            error=str(exc),
            error_code="MANIFEST_INVALID",
        )

    return IndexHealth(
        healthy=True,
        snapshot_id=str(snapshot.id),
        document_count=snapshot.document_count,
        chunk_count=snapshot.chunk_count,
    )


async def reconcile_stale_jobs(session: AsyncSession) -> int:
    """Find and mark stale RUNNING jobs as FAILED (spec §10).

    On startup, any job stuck in RUNNING state is marked FAILED so it
    can be retried. Returns the count of reconciled jobs.
    """
    from datetime import UTC, datetime

    from app.models.enums import JobStatus
    from app.models.job import IngestionJob

    stale_q = await session.execute(
        select(IngestionJob).where(IngestionJob.status == JobStatus.RUNNING)
    )
    stale_jobs = stale_q.scalars().all()
    count = 0
    for job in stale_jobs:
        job.status = JobStatus.FAILED
        job.error_code = "STALE_ON_RESTART"
        job.error_message = "Job was RUNNING on startup, marked stale"
        job.finished_at = datetime.now(UTC)
        count += 1
    if count:
        await session.flush()
        logger.warning("reconciled_stale_jobs", count=count)
    return count


async def reconcile_v2_builds(session: AsyncSession, artifact_manager: object) -> tuple[int, int]:
    """Fail stale building versions/snapshots and quarantine staged IR on restart."""
    from datetime import UTC, datetime

    versions = list(
        (
            await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.status == DocumentVersionStatus.BUILDING
                )
            )
        )
        .scalars()
        .all()
    )
    snapshots = list(
        (
            await session.execute(
                select(IndexSnapshot).where(IndexSnapshot.status == IndexSnapshotStatus.BUILDING)
            )
        )
        .scalars()
        .all()
    )
    for version in versions:
        version.status = DocumentVersionStatus.FAILED
        version.failed_at = datetime.now(UTC)
        fail = getattr(artifact_manager, "fail", None)
        if callable(fail):
            fail(version.id, version.id)
    for snapshot in snapshots:
        snapshot.status = IndexSnapshotStatus.FAILED
    known_versions = set((await session.execute(select(DocumentVersion.id))).scalars().all())
    quarantine = getattr(artifact_manager, "quarantine_orphans", None)
    if callable(quarantine):
        quarantine(known_versions)
    if versions or snapshots:
        await session.flush()
        logger.warning("reconciled_v2_builds", versions=len(versions), snapshots=len(snapshots))
    return len(versions), len(snapshots)


async def check_document_consistency(session: AsyncSession) -> list[str]:
    """Find documents in inconsistent states (spec §10, §13.3).

    Returns a list of document IDs whose active_document_version_id points
    to a version that is not READY.
    """
    from app.models.document import Document

    inconsistent: list[str] = []
    q = await session.execute(
        select(Document).where(
            Document.status == DocumentStatus.READY,
            Document.active_document_version_id.is_not(None),
        )
    )
    for doc in q.scalars().all():
        ver = await session.get(DocumentVersion, doc.active_document_version_id)
        if ver is None or ver.status != DocumentVersionStatus.READY:
            inconsistent.append(str(doc.id))
    return inconsistent
