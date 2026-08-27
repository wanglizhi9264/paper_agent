from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embedding.base import EmbeddingProvider
from app.index.faiss_index import FaissIndex
from app.index.snapshot import (
    atomic_activate_snapshot,
    build_manifest,
    save_manifest,
    validate_manifest,
)
from app.models.chunk import Chunk, DocumentVersion
from app.models.document import Document
from app.models.enums import DocumentStatus, DocumentVersionStatus, IndexSnapshotStatus
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.retrieval.bm25 import BM25Index


async def build_corpus_snapshot(
    session: AsyncSession,
    *,
    pending_document: Document,
    pending_version: DocumentVersion,
    embedding_provider: EmbeddingProvider,
    indexes_dir: Path,
) -> IndexSnapshot:
    """Build one immutable FAISS/BM25 snapshot for the whole compatible corpus."""
    await session.flush()
    manifest_model = embedding_provider.manifest
    if pending_version.ir_schema_version == 2 and not pending_version.parser_signature:
        raise ValueError("V2 DocumentVersion requires parser_signature before snapshot build")

    rows = (
        await session.execute(
            select(Document, DocumentVersion)
            .join(DocumentVersion, Document.active_document_version_id == DocumentVersion.id)
            .where(
                Document.status == DocumentStatus.READY,
                DocumentVersion.status == DocumentVersionStatus.READY,
                DocumentVersion.embedding_signature == manifest_model.signature,
            )
        )
    ).all()
    versions: dict[uuid.UUID, DocumentVersion] = {doc.id: version for doc, version in rows}
    versions[pending_document.id] = pending_version

    version_ids = [version.id for version in versions.values()]
    chunks = list(
        (
            await session.execute(
                select(Chunk)
                .where(Chunk.document_version_id.in_(version_ids))
                .order_by(Chunk.document_id, Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    chunkable = [
        chunk
        for chunk in chunks
        if not (
            chunk.metadata_
            and (
                chunk.metadata_.get("code_not_add_index")
                or chunk.metadata_.get("chunk_subtype") == "table_parent"
            )
        )
    ]
    if not chunkable:
        raise ValueError("cannot activate an empty corpus snapshot")

    max_id = (await session.execute(select(func.max(Chunk.faiss_id)))).scalar() or -1
    next_id = int(max_id) + 1
    for chunk in chunkable:
        if chunk.faiss_id is None:
            chunk.faiss_id = next_id
            next_id += 1
    await session.flush()

    vectors = embedding_provider.embed_texts(
        [chunk.retrieval_content for chunk in chunkable], is_query=False
    ).vectors
    assigned_ids: list[int] = []
    for chunk in chunkable:
        if chunk.faiss_id is None:
            raise ValueError("chunk has no faiss id after allocation")
        assigned_ids.append(chunk.faiss_id)
    faiss_ids = np.asarray(assigned_ids, dtype=np.int64)
    faiss_index = FaissIndex.create(manifest_model.dimension)
    faiss_index.add_texts(vectors, faiss_ids, normalize=False)

    snapshot_id = uuid.uuid4()
    building_dir = indexes_dir / "building" / str(snapshot_id)
    building_dir.mkdir(parents=True, exist_ok=False)
    faiss_path = building_dir / "index.faiss"
    bm25_path = building_dir / "bm25.json"
    manifest_path = building_dir / "manifest.json"
    faiss_index.save(faiss_path)

    bm25 = BM25Index()
    bm25.build(
        list(zip(assigned_ids, [chunk.retrieval_content for chunk in chunkable], strict=True))
    )
    bm25_path.write_text(
        json.dumps(bm25.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    version_map = {str(doc_id): str(version.id) for doc_id, version in versions.items()}
    manifest = build_manifest(
        manifest_embedding=manifest_model,
        faiss_path=faiss_path,
        bm25_path=bm25_path,
        analyzer={"name": "simple", "k1": 1.5, "b": 0.75},
        document_versions=version_map,
        document_count=len(version_map),
        chunk_count=len(chunkable),
        max_faiss_id=max(assigned_ids),
    )
    save_manifest(manifest, manifest_path)
    validate_manifest(
        manifest,
        faiss_path=faiss_path,
        expected_embedding_signature=manifest_model.signature,
        expected_dimension=manifest_model.dimension,
        db_document_versions=version_map,
    )
    if not bm25_path.exists() or BM25Index.from_dict(
        json.loads(bm25_path.read_text(encoding="utf-8"))
    ).stats.n_docs != len(chunkable):
        raise ValueError("BM25 snapshot validation failed")

    active_dir = indexes_dir / "versions" / str(snapshot_id)
    atomic_activate_snapshot(
        building_dir=building_dir,
        active_dir=active_dir,
        faiss_filename=faiss_path.name,
        manifest_filename=manifest_path.name,
        bm25_filename=bm25_path.name,
    )
    faiss_path = active_dir / faiss_path.name
    bm25_path = active_dir / bm25_path.name

    snapshot = IndexSnapshot(
        id=snapshot_id,
        status=IndexSnapshotStatus.BUILDING,
        embedding_signature=manifest_model.signature,
        faiss_path=str(faiss_path),
        bm25_path=str(bm25_path),
        manifest_sha256=manifest.sha256,
        manifest=manifest.to_dict(),
        document_count=len(version_map),
        chunk_count=len(chunkable),
        max_faiss_id=max(assigned_ids),
        activated_at=None,
    )
    session.add(snapshot)
    await session.flush()

    return snapshot


async def activate_snapshot_record(session: AsyncSession, snapshot: IndexSnapshot) -> None:
    """Switch the singleton DB pointer after shadow files have been activated."""
    if snapshot.status != IndexSnapshotStatus.BUILDING:
        raise ValueError("only a building snapshot can be activated")
    state = await session.get(SystemState, 1)
    if state is None:
        state = SystemState(id=1)
        session.add(state)
    prior_active_snapshot_id = state.active_index_snapshot_id
    if prior_active_snapshot_id is not None:
        old = await session.get(IndexSnapshot, state.active_index_snapshot_id)
        if old is not None and old.status == IndexSnapshotStatus.ACTIVE:
            old.status = IndexSnapshotStatus.SUPERSEDED
    state.active_index_snapshot_id = snapshot.id
    snapshot.manifest = {
        **(snapshot.manifest or {}),
        "prior_active_snapshot_id": (
            str(prior_active_snapshot_id) if prior_active_snapshot_id is not None else None
        ),
    }
    snapshot.status = IndexSnapshotStatus.ACTIVE
    snapshot.activated_at = datetime.now(UTC)
    await session.flush()
