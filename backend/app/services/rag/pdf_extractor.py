"""PDF text extraction (pypdf) and text cleaning."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.logging import get_logger

logger = get_logger(__name__)

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile(r"[ \t\f\v\u00a0]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")


class PdfExtractionError(Exception):
    """The PDF could not be read at all."""


@dataclass(slots=True)
class ExtractedPage:
    page_number: int  # 1-based
    text: str


@dataclass(slots=True)
class ExtractedDocument:
    page_count: int
    pages: list[ExtractedPage]
    warnings: list[str] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return any(page.text for page in self.pages)


def clean_text(raw: str) -> str:
    """Normalize unicode, join hyphenated line breaks, collapse whitespace.

    Paragraph breaks (blank lines) are kept; single line breaks inside a
    paragraph become spaces.
    """
    text = unicodedata.normalize("NFKC", raw)
    text = _CONTROL.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    paragraphs = re.split(r"\n\s*\n", text)
    cleaned: list[str] = []
    for paragraph in paragraphs:
        joined = _SPACES.sub(" ", paragraph.replace("\n", " ")).strip()
        if joined:
            cleaned.append(joined)
    return _MANY_NEWLINES.sub("\n\n", "\n\n".join(cleaned))


def extract_pdf(path: Path) -> ExtractedDocument:
    """Extract cleaned text page by page. Pages that fail are skipped with a warning."""
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise PdfExtractionError("PDF is password protected.")
            except PdfExtractionError:
                raise
            except Exception as exc:  # pypdf raises several types for crypto issues
                raise PdfExtractionError(f"PDF is encrypted and cannot be read: {exc}") from exc
        total = len(reader.pages)
    except PdfExtractionError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise PdfExtractionError(f"Unreadable PDF: {exc}") from exc

    pages: list[ExtractedPage] = []
    warnings: list[str] = []
    for index in range(total):
        try:
            raw = reader.pages[index].extract_text() or ""
        except Exception as exc:  # malformed page objects raise many exception types
            warnings.append(f"page {index + 1}: extraction failed ({exc.__class__.__name__})")
            continue
        text = clean_text(raw)
        if text:
            pages.append(ExtractedPage(page_number=index + 1, text=text))
    if total and not pages:
        warnings.append("no extractable text (scanned PDF? OCR is not enabled)")
    return ExtractedDocument(page_count=total, pages=pages, warnings=warnings)
