from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from the environment.

    All variables use the ``PAPER_RAG_`` prefix. Missing required values fail fast
    at startup. No ``*_API_KEY`` is ever logged or serialized.
    """

    model_config = SettingsConfigDict(
        env_prefix="PAPER_RAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = "development"
    log_level: str = "INFO"

    # --- Storage ---
    storage_dir: Path = Field(default=Path("./storage"))
    max_upload_bytes: int = 104_857_600

    # --- Infrastructure ---
    database_url: str = Field(min_length=1)
    redis_url: str = Field(min_length=1)

    # --- Embedding ---
    embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_revision: str = Field(default="")
    embedding_device: str = "cuda:0"
    embedding_batch_size: int = 16
    embedding_dtype: str = "float16"

    # --- Reranker ---
    rerank_model: str = "BAAI/bge-reranker-base"
    rerank_revision: str = Field(default="")
    rerank_device: str = "cuda:0"
    rerank_batch_size: int = 4
    rerank_dtype: str = "float16"

    # --- Generator (OpenAI-compatible) ---
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = "qwen3:4b-instruct"
    llm_api_key: str = Field(default="paper-rag-local")
    llm_context_tokens: int = 8192
    llm_request_timeout_seconds: float = 120.0

    # --- GPU ---
    cuda_device: str = "cuda:0"
    gpu_max_concurrency: int = 1

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["http://127.0.0.1:5173"])

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.gpu_max_concurrency < 1:
            raise ValueError("PAPER_RAG_GPU_MAX_CONCURRENCY must be >= 1")
        if self.max_upload_bytes <= 0 or self.max_upload_bytes > 104_857_600:
            raise ValueError("PAPER_RAG_MAX_UPLOAD_BYTES must be in 1..104857600")
        if self.env not in {"development", "test", "production"}:
            raise ValueError("PAPER_RAG_ENV must be one of development|test|production")
        if not (
            self.llm_base_url.startswith("http://") or self.llm_base_url.startswith("https://")
        ):
            raise ValueError("PAPER_RAG_LLM_BASE_URL must be an http(s) URL")
        return self

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def indexes_dir(self) -> Path:
        return self.storage_dir / "indexes"

    @property
    def tmp_dir(self) -> Path:
        return self.storage_dir / "tmp"

    def ensure_storage_dirs(self) -> None:
        for p in (self.storage_dir, self.uploads_dir, self.indexes_dir, self.tmp_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance. Use ``reset_settings_cache`` in tests."""
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    get_settings.cache_clear()
