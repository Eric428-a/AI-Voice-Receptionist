"""Recursive, category-free discovery of PDFs in the knowledge directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger
from app.utils.file_utils import is_within_directory, to_posix_relative

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoveredDocument:
    path: Path
    relative_path: str
    filename: str
    size_bytes: int
    modified_ns: int


def discover_pdfs(root: Path) -> list[DiscoveredDocument]:
    """Find every ``*.pdf`` (any case) under ``root``, recursively.

    Hidden files/folders are skipped, and symlinks that escape the knowledge
    directory are ignored so the index never reads files outside it.
    """
    if not root.exists() or not root.is_dir():
        logger.warning("Knowledge directory does not exist", extra={"path": str(root)})
        return []
    documents: list[DiscoveredDocument] = []
    for path in root.rglob("*"):
        relative_parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in relative_parts):
            continue
        if path.suffix.lower() != ".pdf" or not path.is_file():
            continue
        if not is_within_directory(path, root):
            logger.warning("Skipping PDF outside knowledge directory", extra={"path": str(path)})
            continue
        stat = path.stat()
        documents.append(
            DiscoveredDocument(
                path=path,
                relative_path=to_posix_relative(path, root),
                filename=path.name,
                size_bytes=stat.st_size,
                modified_ns=stat.st_mtime_ns,
            )
        )
    documents.sort(key=lambda doc: doc.relative_path.lower())
    return documents


def directory_signature(documents: list[DiscoveredDocument]) -> tuple[tuple[str, int, int], ...]:
    """Cheap fingerprint used by the watcher to detect added/changed/removed files."""
    return tuple((d.relative_path, d.size_bytes, d.modified_ns) for d in documents)
