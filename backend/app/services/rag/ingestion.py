"""Incremental, duplicate-aware ingestion of every PDF in the knowledge folder.

A JSON manifest beside the Chroma data records each document's hash, so
unchanged files are skipped, changed files are re-indexed, deleted files are
removed from the index and byte-identical copies are indexed only once.
Changing the embedding model triggers a full re-index automatically.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.knowledge import DocumentRecord, IngestionReport, KnowledgeStatus
from app.services.rag.document_loader import DiscoveredDocument, directory_signature, discover_pdfs
from app.services.rag.embeddings import EmbeddingProvider
from app.services.rag.pdf_extractor import PdfExtractionError, extract_pdf
from app.services.rag.text_chunker import TextChunker
from app.services.rag.vector_store import ChromaVectorStore, StoredChunk
from app.utils.file_utils import atomic_write_json, read_json, sha256_file

logger = get_logger(__name__)

MANIFEST_VERSION = 1


class IngestionInProgressError(Exception):
    """Raised when an ingestion run is requested while another is running."""


def chunk_id_for(document_hash: str, chunk_index: int) -> str:
    return hashlib.sha256(f"{document_hash}:{chunk_index}".encode()).hexdigest()[:32]


class KnowledgeManifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.embedding_model: str | None = None
        self.documents: dict[str, DocumentRecord] = {}
        self.last_report: IngestionReport | None = None

    def load(self) -> None:
        data: Any = None
        try:
            data = read_json(self.path)
        except (OSError, ValueError) as exc:
            logger.error("Manifest unreadable; a full re-index will run", extra={"error": str(exc)})
        if not isinstance(data, dict):
            return
        self.embedding_model = data.get("embedding_model")
        self.documents = {
            key: DocumentRecord.model_validate(value)
            for key, value in (data.get("documents") or {}).items()
        }
        if data.get("last_report"):
            self.last_report = IngestionReport.model_validate(data["last_report"])

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {
                "version": MANIFEST_VERSION,
                "embedding_model": self.embedding_model,
                "documents": {k: v.model_dump(mode="json") for k, v in self.documents.items()},
                "last_report": self.last_report.model_dump(mode="json") if self.last_report else None,
            },
        )


class IngestionService:
    def __init__(
        self,
        settings: Settings,
        embedder: EmbeddingProvider,
        store: ChromaVectorStore,
        chunker: TextChunker | None = None,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        self._store = store
        self._chunker = chunker or TextChunker(settings.rag_chunk_size, settings.rag_chunk_overlap)
        self._manifest = KnowledgeManifest(settings.vector_store_path / "manifest.json")
        self._manifest.load()
        self._lock = asyncio.Lock()
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    @property
    def indexed_document_count(self) -> int:
        return sum(1 for d in self._manifest.documents.values() if d.status == "indexed")

    async def ingest(self, force: bool = False) -> IngestionReport:
        if self._lock.locked():
            raise IngestionInProgressError("An ingestion run is already in progress.")
        async with self._lock:
            try:
                report = await self._run(force)
                self.last_error = None
                return report
            except Exception as exc:
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                logger.exception("Knowledge ingestion failed")
                raise

    async def _run(self, force: bool) -> IngestionReport:
        started = datetime.now(timezone.utc)
        root = self._settings.knowledge_directory
        manifest = self._manifest
        full_reindex = force or manifest.embedding_model != self._embedder.identifier
        if full_reindex and (manifest.documents or await self._store.count()):
            logger.info("Running full re-index", extra={"embedding": self._embedder.identifier})
            await self._store.reset()
            manifest.documents = {}
        manifest.embedding_model = self._embedder.identifier

        discovered = await asyncio.to_thread(discover_pdfs, root)
        report = IngestionReport(
            started_at=started, finished_at=started, discovered=len(discovered), full_reindex=full_reindex
        )
        seen_paths = {doc.relative_path for doc in discovered}
        for removed_path in sorted(set(manifest.documents) - seen_paths):
            await self._store.delete_document(removed_path)
            del manifest.documents[removed_path]
            report.removed.append(removed_path)

        hash_owner: dict[str, str] = {}
        for doc in discovered:
            await self._ingest_one(doc, hash_owner, report)

        report.total_chunks = await self._store.count()
        report.finished_at = datetime.now(timezone.utc)
        manifest.last_report = report
        await asyncio.to_thread(manifest.save)
        logger.info(
            "Knowledge ingestion finished",
            extra={
                "discovered": report.discovered,
                "added": len(report.added),
                "updated": len(report.updated),
                "removed": len(report.removed),
                "failed": len(report.failed),
                "chunks": report.total_chunks,
            },
        )
        return report

    async def _ingest_one(
        self, doc: DiscoveredDocument, hash_owner: dict[str, str], report: IngestionReport
    ) -> None:
        manifest = self._manifest
        previous = manifest.documents.get(doc.relative_path)
        now = datetime.now(timezone.utc)

        if doc.size_bytes > self._settings.max_pdf_size_bytes:
            if previous and previous.status == "indexed":
                await self._store.delete_document(doc.relative_path)
            manifest.documents[doc.relative_path] = DocumentRecord(
                relative_path=doc.relative_path, filename=doc.filename, document_hash="",
                status="too_large", ingested_at=now,
                error=f"File exceeds MAX_PDF_SIZE_MB ({self._settings.max_pdf_size_mb} MB)",
            )
            report.failed[doc.relative_path] = "file too large"
            return

        try:
            digest = await asyncio.to_thread(sha256_file, doc.path)
        except OSError as exc:
            report.failed[doc.relative_path] = f"unreadable: {exc}"
            return

        owner = hash_owner.get(digest)
        if owner is not None:
            if previous and previous.status == "indexed":
                await self._store.delete_document(doc.relative_path)
            manifest.documents[doc.relative_path] = DocumentRecord(
                relative_path=doc.relative_path, filename=doc.filename, document_hash=digest,
                status="duplicate", ingested_at=now, duplicate_of=owner,
            )
            report.duplicates.append(doc.relative_path)
            return
        hash_owner[digest] = doc.relative_path

        if previous and previous.document_hash == digest and previous.status in ("indexed", "empty"):
            report.unchanged.append(doc.relative_path)
            return

        try:
            extracted = await asyncio.to_thread(extract_pdf, doc.path)
            chunks = self._chunker.chunk_pages(extracted.pages)
            vectors = await self._embedder.embed_documents([c.text for c in chunks])
        except PdfExtractionError as exc:
            await self._record_failure(doc, digest, str(exc), report, previous)
            return
        except Exception as exc:
            await self._record_failure(doc, digest, f"{exc.__class__.__name__}: {exc}", report, previous)
            if not isinstance(exc, (ValueError, OSError)):
                raise  # provider/config errors abort the run so they are visible
            return

        await self._store.delete_document(doc.relative_path)
        stamp = now.isoformat()
        await self._store.upsert(
            [
                StoredChunk(
                    chunk_id=chunk_id_for(digest, chunk.chunk_index),
                    text=chunk.text,
                    embedding=vector,
                    metadata={
                        "filename": doc.filename,
                        "relative_path": doc.relative_path,
                        "page_number": chunk.page_number,
                        "chunk_index": chunk.chunk_index,
                        "document_hash": digest,
                        "ingested_at": stamp,
                    },
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )
        manifest.documents[doc.relative_path] = DocumentRecord(
            relative_path=doc.relative_path, filename=doc.filename, document_hash=digest,
            status="indexed" if chunks else "empty", page_count=extracted.page_count,
            chunk_count=len(chunks), ingested_at=now,
            error="; ".join(extracted.warnings) or None,
        )
        (report.updated if previous else report.added).append(doc.relative_path)

    async def _record_failure(
        self,
        doc: DiscoveredDocument,
        digest: str,
        error: str,
        report: IngestionReport,
        previous: DocumentRecord | None,
    ) -> None:
        if previous and previous.status == "indexed":
            await self._store.delete_document(doc.relative_path)
        self._manifest.documents[doc.relative_path] = DocumentRecord(
            relative_path=doc.relative_path, filename=doc.filename, document_hash=digest,
            status="failed", ingested_at=datetime.now(timezone.utc), error=error,
        )
        report.failed[doc.relative_path] = error
        logger.warning("PDF ingestion failed", extra={"path": doc.relative_path, "error": error})

    async def status(self) -> KnowledgeStatus:
        documents = sorted(self._manifest.documents.values(), key=lambda d: d.relative_path)
        return KnowledgeStatus(
            knowledge_directory_exists=self._settings.knowledge_directory.is_dir(),
            embedding_model=self._embedder.identifier,
            indexed_documents=self.indexed_document_count,
            total_chunks=await self._store.count(),
            failed_documents=sum(1 for d in documents if d.status in ("failed", "too_large")),
            ingestion_running=self.running,
            last_ingestion=self._manifest.last_report,
            last_error=self.last_error,
            documents=documents,
        )


class KnowledgeWatcher:
    """Polls the knowledge folder and re-ingests when files are added, changed or removed."""

    def __init__(self, service: IngestionService, root: Path, interval_seconds: float) -> None:
        self._service = service
        self._root = root
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._signature: tuple[tuple[str, int, int], ...] | None = None

    def start(self) -> None:
        if self._interval > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="knowledge-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        self._signature = directory_signature(await asyncio.to_thread(discover_pdfs, self._root))
        while True:
            await asyncio.sleep(self._interval)
            try:
                signature = directory_signature(await asyncio.to_thread(discover_pdfs, self._root))
                if signature != self._signature and not self._service.running:
                    logger.info("Knowledge folder changed; re-indexing")
                    await self._service.ingest()
                    self._signature = signature
            except IngestionInProgressError:
                continue
            except Exception:
                logger.exception("Automatic knowledge ingestion failed")
                # Retry after the next interval by not updating the signature.
