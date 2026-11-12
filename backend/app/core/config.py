"""Environment-based application settings.

Settings are read from environment variables, then from ``<project>/.env`` and
``<project>/backend/.env`` (the backend file wins). Relative paths are resolved
against the project root so the app behaves the same no matter where it is
started from.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


class _SafeFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # General
    environment: Literal["development", "production", "test"] = "development"
    log_level: str = "INFO"
    log_transcripts: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    admin_api_key: SecretStr | None = None

    # Company / assistant
    company_name: str = "Haumex Labs Inc."
    company_spoken_name: str = "Haumex Labs"
    assistant_greeting: str = (
        "Hello! Thank you for calling {company_spoken_name}. I'm the company's virtual "
        "assistant. How can I help you today?"
    )

    # OpenRouter
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = ""
    openrouter_app_url: str = "http://localhost:5173"
    openrouter_app_title: str = "Haumex Labs Inc. AI Voice Caller"
    openrouter_timeout_seconds: float = Field(60.0, gt=0)
    openrouter_connect_timeout_seconds: float = Field(10.0, gt=0)
    openrouter_max_retries: int = Field(2, ge=0, le=5)
    llm_temperature: float = Field(0.4, ge=0, le=2)
    llm_max_tokens: int = Field(350, ge=32, le=4096)

    # Embeddings (local model for PDF search; no external provider)
    embedding_provider: Literal["fastembed"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = Field(32, ge=1, le=512)

    # Browser speech (recognition + synthesis happen in the caller's browser)
    speech_language: str = "en-US"
    speech_rate: float = Field(1.0, ge=0.5, le=2.0)

    # Knowledge base / RAG
    knowledge_directory: Path = Path("knowledge/pdfs")
    vector_store_path: Path = Path("data/vector_store")
    chroma_collection: str = "haumex_knowledge"
    rag_chunk_size: int = Field(
        1000, ge=200, le=8000, validation_alias=AliasChoices("rag_chunk_size", "chunk_size")
    )
    rag_chunk_overlap: int = Field(
        150, ge=0, validation_alias=AliasChoices("rag_chunk_overlap", "chunk_overlap")
    )
    retrieval_top_k: int = Field(8, ge=1, le=50)
    retrieval_max_chunks: int = Field(4, ge=1, le=20)
    retrieval_min_score: float = Field(0.30, ge=-1, le=1)
    max_context_chars: int = Field(6000, ge=500)
    max_pdf_size_mb: float = Field(50, gt=0)
    auto_ingest_on_startup: bool = True
    knowledge_watch_interval_seconds: float = Field(60, ge=0)

    # Calls
    max_concurrent_calls: int = Field(20, ge=1)
    max_call_duration_seconds: int = Field(900, ge=30)
    call_setup_timeout_seconds: int = Field(60, ge=5)

    @field_validator("knowledge_directory", "vector_store_path", mode="after")
    @classmethod
    def _resolve_path(cls, value: Path) -> Path:
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @field_validator(
        "openrouter_api_key", "admin_api_key", mode="before",
    )
    @classmethod
    def _empty_secret_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_overlap(self) -> "Settings":
        if self.rag_chunk_overlap >= self.rag_chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE")
        return self

    # Derived helpers -------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.openrouter_api_key and self.openrouter_model.strip())

    @property
    def max_pdf_size_bytes(self) -> int:
        return int(self.max_pdf_size_mb * 1024 * 1024)

    def render_template(self, template: str) -> str:
        return template.format_map(
            _SafeFormatDict(
                company_name=self.company_name,
                company_spoken_name=self.company_spoken_name,
            )
        )

    @property
    def greeting_text(self) -> str:
        return self.render_template(self.assistant_greeting)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
