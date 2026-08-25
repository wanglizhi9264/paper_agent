from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.document_ir.errors import PDF_PARSE_FAILED, ParseError
from app.embedding.fake import FakeEmbeddingAdapter
from app.models.chunk import DocumentVersion
from app.models.document import Document
from app.models.enums import DocumentStatus, DocumentVersionStatus, JobKind, JobStage, JobStatus
from app.models.index_snapshot import SystemState
from app.models.job import IngestionJob
from app.services.ingestion import RealChunker, V2PDFDocumentParser, run_ingest
from app.services.ir_artifacts import IRArtifactManager
from tests.unit.document_ir.builders import make_element, make_ir


def _document_and_job() -> tuple[Document, IngestionJob]:
    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        filename="paper.pdf",
        stored_filename="stored.pdf",
        media_type="application/pdf",
        extension="pdf",
        sha256="a" * 64,
        file_size=128,
        status=DocumentStatus.QUEUED,
    )
    job = IngestionJob(
        id=uuid.uuid4(),
        document_id=document_id,
        kind=JobKind.INGEST,
        status=JobStatus.QUEUED,
        stage=JobStage.QUEUED,
        attempt=1,
    )
    return document, job


class _FakeIRParser:
    def parse(self, _path: Path, *, document_id: uuid.UUID):
        return make_ir(document_id=document_id, elements=[make_element()])


class _FailingIRParser:
    def parse(self, _path: Path, *, document_id: uuid.UUID):
        del document_id
        raise ParseError("bad layout", code=PDF_PARSE_FAILED)


@pytest.mark.asyncio
async def test_pdf_v2_ingest_populates_fields_and_activates_artifacts(
    async_sqlite_session, tmp_path: Path
) -> None:
    document, job = _document_and_job()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    path = uploads / document.stored_filename
    path.write_bytes(b"%PDF fake")
    manager = IRArtifactManager(tmp_path)
    parser = V2PDFDocumentParser(path, manager, parser=_FakeIRParser())
    async_sqlite_session.add_all([document, job])
    await async_sqlite_session.flush()

    await run_ingest(
        async_sqlite_session,
        job,
        document,
        parser=parser,
        chunker=RealChunker(parser),
        artifact_manager=manager,
    )
    await async_sqlite_session.commit()

    assert document.status == DocumentStatus.READY
    version = await async_sqlite_session.get(DocumentVersion, document.active_document_version_id)
    assert version is not None
    assert version.status == DocumentVersionStatus.READY
    assert version.parser_id == "pymupdf"
    assert version.parser_signature and len(version.parser_signature) == 64
    assert version.ir_schema_version == 2
    assert version.ir_path and version.ir_path.startswith("ir/versions/")
    assert version.ir_sha256 and manager.verify(version.ir_path, version.ir_sha256)


@pytest.mark.asyncio
async def test_failed_reindex_keeps_old_version_and_snapshot(
    async_sqlite_session, tmp_path: Path
) -> None:
    document, first_job = _document_and_job()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    path = uploads / document.stored_filename
    path.write_bytes(b"%PDF fake")
    manager = IRArtifactManager(tmp_path)
    async_sqlite_session.add_all([document, first_job])
    await async_sqlite_session.flush()
    embedding = FakeEmbeddingAdapter(dimension=8)
    good = V2PDFDocumentParser(path, manager, parser=_FakeIRParser())
    await run_ingest(
        async_sqlite_session,
        first_job,
        document,
        parser=good,
        chunker=RealChunker(good),
        embedding_provider=embedding,
        indexes_dir=tmp_path / "indexes",
        artifact_manager=manager,
    )
    await async_sqlite_session.commit()
    old_version_id = document.active_document_version_id
    state = await async_sqlite_session.get(SystemState, 1)
    old_snapshot_id = state.active_index_snapshot_id

    second_job = IngestionJob(
        id=uuid.uuid4(),
        document_id=document.id,
        kind=JobKind.REINDEX,
        status=JobStatus.QUEUED,
        stage=JobStage.QUEUED,
        attempt=1,
    )
    async_sqlite_session.add(second_job)
    await async_sqlite_session.flush()
    bad = V2PDFDocumentParser(path, manager, parser=_FailingIRParser())
    await run_ingest(
        async_sqlite_session,
        second_job,
        document,
        parser=bad,
        chunker=RealChunker(bad),
        embedding_provider=embedding,
        indexes_dir=tmp_path / "indexes",
        artifact_manager=manager,
    )
    await async_sqlite_session.commit()

    assert second_job.status == JobStatus.FAILED
    assert document.status == DocumentStatus.READY
    assert document.active_document_version_id == old_version_id
    await async_sqlite_session.refresh(state)
    assert state.active_index_snapshot_id == old_snapshot_id

