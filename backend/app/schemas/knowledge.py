"""Schemas describing the knowledge base and ingestion runs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

DocumentStatus = Literal["indexed", "failed", "duplicate", "empty", "too_large"]


class DocumentRecord(BaseModel):
    relative_path: str
    filename: str
    document_hash: str
    status: DocumentStatus
    page_count: int = 0
    chunk_count: int = 0
    ingested_at: datetime
    duplicate_of: str | None = None
    error: str | None = None


class IngestionReport(BaseModel):
    started_at: datetime
    finished_at: datetime
    discovered: int = 0
    added: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    duplicates: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict)
    total_chunks: int = 0
    full_reindex: bool = False


class KnowledgeStatus(BaseModel):
    knowledge_directory_exists: bool
    embedding_model: str
    indexed_documents: int
    total_chunks: int
    failed_documents: int
    ingestion_running: bool
    last_ingestion: IngestionReport | None = None
    last_error: str | None = None
    documents: list[DocumentRecord] = Field(default_factory=list)


class IngestRequest(BaseModel):
    force: bool = False
