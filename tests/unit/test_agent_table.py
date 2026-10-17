from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.agent.contracts import Action, AgentError
from app.models.chunk import Chunk, DocumentVersion
from app.services.ir_artifacts import IRArtifactManager
from tests.unit.document_ir.builders import make_cell, make_element, make_ir, make_table_data
from tests.unit.test_agent_trial_store import setup_trial


async def test_table_outline_and_targeted_rows_exclude_unrelated_headers(
    async_sqlite_session, tmp_path
):
    db = async_sqlite_session
    _, tools, _, docs = await setup_trial(db, tmp_path)
    tools.settings = tools.settings.model_copy(update={"storage_dir": tmp_path})
    cells = [
        make_cell(0, 0, "Dataset", is_column_header=True),
        make_cell(0, 1, "Method", is_column_header=True),
        make_cell(0, 2, "Accuracy", is_column_header=True),
    ]
    cells.append(
        make_cell(1, 0, "Dataset A", is_row_header=True).model_copy(update={"row_span": 19})
    )
    for row in range(1, 20):
        cells.extend(
            [
                make_cell(row, 1, f"Method {row}", is_row_header=True),
                make_cell(row, 2, f"{row}.5 %"),
            ]
        )
    table = make_table_data(cells, row_count=20, column_count=3, header_rows=[0])
    element = make_element(kind="table", text=table.markdown, table=table)
    ir = make_ir(elements=[element]).model_copy(update={"document_id": docs[0].id})
    version = await db.get(DocumentVersion, docs[0].active_document_version_id)
    record = IRArtifactManager(tmp_path).stage(version.id, ir)
    version.ir_path, version.ir_sha256 = record.ir_relative_path, record.ir_sha256
    chunk = await db.scalar(
        select(Chunk).where(Chunk.document_version_id == version.id).order_by(Chunk.chunk_index)
    )
    chunk.metadata_ = {"element_id": str(element.id)}
    await db.commit()
    outline = await tools.table_outline(db, chunk)
    assert isinstance(outline, dict)
    assert outline["row_count"] == 20
    assert "19.5 %" not in str(outline)
    assert "Method 19" in str(outline)
    oversized = await tools.execute(
        Action(tool="read_table", arguments={"evidence_id": str(chunk.id)})
    )
    assert oversized.status == "error"
    assert oversized.error_code == "TABLE_SELECTION_TOO_LARGE"
    assert oversized.evidence == []
    result = await tools.execute(
        Action(tool="read_table", arguments={"evidence_id": str(chunk.id), "rows": [19]})
    )
    source = result.evidence[0].source
    assert "Dataset A" in source.content
    assert "Method 19" in source.content
    assert "19.5 %" in source.content
    assert "Method 18" not in source.content
    assert len(source.cell_ids) == 6


async def test_read_table_preserves_numeric_value_headers_and_provenance(
    async_sqlite_session, tmp_path
):
    db = async_sqlite_session
    _, tools, _, docs = await setup_trial(db, tmp_path)
    tools.settings = tools.settings.model_copy(update={"storage_dir": tmp_path})
    cells = [
        make_cell(0, 0, "Model", is_column_header=True),
        make_cell(0, 1, "Accuracy (%)", is_column_header=True),
        make_cell(1, 0, "Method A", is_row_header=True),
        make_cell(1, 1, "91.7 %"),
    ]
    table = make_table_data(cells, row_count=2, column_count=2, header_rows=[0])
    element = make_element(kind="table", text=table.markdown, table=table)
    ir = make_ir(elements=[element]).model_copy(update={"document_id": docs[0].id})
    version = await db.get(DocumentVersion, docs[0].active_document_version_id)
    record = IRArtifactManager(tmp_path).stage(version.id, ir)
    version.ir_path, version.ir_sha256 = record.ir_relative_path, record.ir_sha256
    chunk = await db.scalar(
        select(Chunk).where(Chunk.document_version_id == version.id).order_by(Chunk.chunk_index)
    )
    chunk.metadata_ = {"element_id": str(element.id)}
    await db.commit()
    result = await tools.execute(
        Action(tool="read_table", arguments={"evidence_id": str(chunk.id), "rows": [1]})
    )
    source = result.evidence[0].source
    assert "91.7 %" in source.content
    assert "Accuracy (%)" in source.content
    assert source.element_id == element.id
    assert set(source.cell_ids) == {cell.id for cell in cells}
    assert source.bboxes
    assert source.page_start == 1
    with pytest.raises(AgentError, match="TOOL_ARGUMENT_INVALID"):
        await tools.execute(
            Action(
                tool="read_table",
                arguments={"evidence_id": str(chunk.id), "cell_ids": [str(uuid.uuid4())]},
            )
        )
