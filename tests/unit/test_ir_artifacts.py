from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.document_ir.errors import IR_ARTIFACT_INVALID, ParseError
from app.services.ir_artifacts import IRArtifactManager
from tests.unit.document_ir.builders import make_element, make_ir


def test_stage_verify_and_atomic_activate(tmp_path: Path) -> None:
    manager = IRArtifactManager(tmp_path)
    version_id = uuid4()
    ir = make_ir(elements=[make_element()])

    staged = manager.stage(version_id, ir)
    assert staged.ir_relative_path.startswith("ir/building/")
    assert manager.verify(staged.ir_relative_path, staged.ir_sha256).document_id == ir.document_id
    assert (tmp_path / staged.ir_relative_path).is_file()

    activated = manager.activate(version_id, staged.parser_signature)
    assert activated.ir_relative_path.startswith("ir/versions/")
    assert not (tmp_path / "ir" / "building" / str(version_id)).exists()
    assert manager.verify(activated.ir_relative_path, activated.ir_sha256).document_id == ir.document_id


def test_tampered_artifact_is_rejected(tmp_path: Path) -> None:
    manager = IRArtifactManager(tmp_path)
    staged = manager.stage(uuid4(), make_ir(elements=[make_element()]))
    (tmp_path / staged.ir_relative_path).write_text("{}", encoding="utf-8")
    with pytest.raises(ParseError) as exc_info:
        manager.verify(staged.ir_relative_path, staged.ir_sha256)
    assert exc_info.value.code == IR_ARTIFACT_INVALID


def test_failed_build_moves_only_its_version_to_failed_area(tmp_path: Path) -> None:
    manager = IRArtifactManager(tmp_path)
    version_id = uuid4()
    job_id = uuid4()
    manager.stage(version_id, make_ir(elements=[make_element()]))
    failed = manager.fail(version_id, job_id)
    assert failed is not None and failed.is_dir()
    assert failed.is_relative_to(tmp_path / "tmp" / "failed")
    assert not (tmp_path / "ir" / "building" / str(version_id)).exists()


def test_paths_outside_storage_are_never_accepted(tmp_path: Path) -> None:
    manager = IRArtifactManager(tmp_path)
    with pytest.raises(ParseError) as exc_info:
        manager.verify("../../private.json", "a" * 64)
    assert exc_info.value.code == IR_ARTIFACT_INVALID


def test_activation_refuses_overwrite(tmp_path: Path) -> None:
    manager = IRArtifactManager(tmp_path)
    version_id = uuid4()
    ir = make_ir(elements=[make_element()])
    manager.stage(version_id, ir)
    manager.activate(version_id, ir.parser.signature)
    with pytest.raises(ParseError, match="already exists"):
        manager.activate(version_id, ir.parser.signature)


def test_remove_version_cleans_building_and_active_artifacts(tmp_path: Path) -> None:
    manager = IRArtifactManager(tmp_path)
    first = uuid4()
    second = uuid4()
    ir = make_ir(elements=[make_element()])
    manager.stage(first, ir)
    manager.stage(second, ir)
    manager.activate(second, ir.parser.signature)
    manager.remove_version(first)
    manager.remove_version(second)
    assert not (tmp_path / "ir" / "building" / str(first)).exists()
    assert not (tmp_path / "ir" / "versions" / str(second)).exists()


def test_quarantine_orphans_preserves_known_versions(tmp_path: Path) -> None:
    manager = IRArtifactManager(tmp_path)
    known = uuid4()
    orphan = uuid4()
    ir = make_ir(elements=[make_element()])
    manager.stage(known, ir)
    manager.stage(orphan, ir)
    assert manager.quarantine_orphans({known}) == 1
    assert (tmp_path / "ir" / "building" / str(known)).is_dir()
    assert (tmp_path / "tmp" / "failed" / "orphans" / f"building-{orphan}").is_dir()
