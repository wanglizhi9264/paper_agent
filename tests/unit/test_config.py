from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_defaults_loaded_from_test_env() -> None:
    from app.core.config import get_settings

    settings = get_settings()
    assert settings.env == "test"
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.redis_url.startswith("redis://")
    assert settings.max_upload_bytes == 104_857_600
    assert settings.gpu_max_concurrency == 1
    assert settings.host == "127.0.0.1"


def test_invalid_env_rejected() -> None:
    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            env="weird",
            database_url="postgresql+asyncpg://u:p@127.0.0.1/db",
            redis_url="redis://127.0.0.1:6379/0",
        )


def test_llm_base_url_must_be_http() -> None:
    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://u:p@127.0.0.1/db",
            redis_url="redis://127.0.0.1:6379/0",
            llm_base_url="ftp://nope",
        )


def test_storage_subpaths_resolve_under_root(tmp_path) -> None:
    from app.core.config import Settings

    s = Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@127.0.0.1/db",
        redis_url="redis://127.0.0.1:6379/0",
        storage_dir=tmp_path,
    )
    assert s.uploads_dir == tmp_path / "uploads"
    assert s.indexes_dir == tmp_path / "indexes"
    assert s.tmp_dir == tmp_path / "tmp"
    s.ensure_storage_dirs()
    assert s.uploads_dir.is_dir()
