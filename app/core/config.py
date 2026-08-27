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

    # --- PDF Ingestion V2 (spec pdf-ingestion-v2 §14) ---
    pdf_ir_schema_version: int = 2
    pdf_parser: str = "auto"
    pdf_layout_parser: str = "docling"
    pdf_normalizer_version: str = "unicode-v2"
    pdf_fast_path_min_reading_order_confidence: float = 0.95
    pdf_max_orphan_numeric_ratio: float = 0.05
    pdf_max_replacement_characters: int = 0

    # --- Docling layout parser (spec pdf-ingestion-v2 §7.2, §14) ---
    docling_ocr: bool = False
    docling_table_structure: bool = True
    docling_formula_enrichment: bool = True
    docling_pymupdf_table_fallback: bool = True
    docling_device: str = "cpu"
    docling_layout_model: str = "docling-project/docling-layout-heron"
    docling_table_model: str = "docling-project/TableFormer"
    # Resolved by the explicit setup command (`python -m app.cli.docling_setup`);
    # empty revisions keep A/B usable but must be pinned before production (§14).
    docling_layout_revision: str = Field(default="")
    docling_table_revision: str = Field(default="")
    docling_artifacts_path: str = ""

    # --- MinerU isolated challenger (spec pdf-ingestion-v2 §7.3, §14) ---
    mineru_enabled: bool = False
    mineru_command: str = "mineru"
    mineru_backend: str = "pipeline"
    mineru_timeout_seconds: int = 900
    mineru_parser_version: str = ""
    mineru_model_revision: str = ""

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
        if self.pdf_parser not in {"auto", "pymupdf", "docling", "mineru"}:
            raise ValueError("PAPER_RAG_PDF_PARSER must be one of auto|pymupdf|docling|mineru")
        if self.pdf_ir_schema_version != 2:
            raise ValueError("PAPER_RAG_PDF_IR_SCHEMA_VERSION must be 2")
        if self.pdf_normalizer_version != "unicode-v2":
            raise ValueError("PAPER_RAG_PDF_NORMALIZER_VERSION must be unicode-v2")
        if not (0.0 <= self.pdf_fast_path_min_reading_order_confidence <= 1.0):
            raise ValueError("PAPER_RAG_PDF_FAST_PATH_MIN_READING_ORDER_CONFIDENCE must be in 0..1")
        if not (0.0 <= self.pdf_max_orphan_numeric_ratio <= 1.0):
            raise ValueError("PAPER_RAG_PDF_MAX_ORPHAN_NUMERIC_RATIO must be in 0..1")
        if self.pdf_max_replacement_characters < 0:
            raise ValueError("PAPER_RAG_PDF_MAX_REPLACEMENT_CHARACTERS must be >= 0")
        if not self.docling_device:
            raise ValueError("PAPER_RAG_DOCLING_DEVICE must not be empty")
        if self.docling_device.lower() not in {"cpu", "cuda:0", "auto"}:
            raise ValueError("PAPER_RAG_DOCLING_DEVICE must be one of cpu|cuda:0|auto")
        if self.mineru_timeout_seconds < 1:
            raise ValueError("PAPER_RAG_MINERU_TIMEOUT_SECONDS must be >= 1")
        if self.pdf_parser == "mineru" and not self.mineru_enabled:
            raise ValueError("PAPER_RAG_MINERU_ENABLED must be true when MinerU is selected")
        if self.pdf_parser == "mineru" and (
            not self.mineru_parser_version or not self.mineru_model_revision
        ):
            raise ValueError(
                "PAPER_RAG_MINERU_PARSER_VERSION and PAPER_RAG_MINERU_MODEL_REVISION "
                "must be pinned when MinerU is selected"
            )
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
