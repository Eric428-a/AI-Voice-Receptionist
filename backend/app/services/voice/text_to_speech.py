"""Turns streamed LLM text into speakable sentences.

Speech itself is produced by the caller's browser (speech synthesis); the
backend only decides where sentences end, so the browser can start speaking
the first sentence while the rest of the reply is still being generated.
"""

from __future__ import annotations

import re

ABBREVIATIONS = {
    "inc", "ltd", "llc", "co", "corp", "dr", "mr", "mrs", "ms", "prof", "st", "jr", "sr",
    "vs", "etc", "e.g", "i.e", "approx", "no", "dept", "est", "u.s", "u.k",
}
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*_`#>|]+")
_BULLET = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.MULTILINE)
_SPACES = re.compile(r"\s+")

# Average conversational speaking speed, used only to estimate how long the
# browser will be talking (safety net if it never reports playback.finished).
WORDS_PER_SECOND = 2.6


def prepare_text_for_speech(text: str) -> str:
    """Remove markdown, links and list markup that should never be read aloud."""
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _BULLET.sub("", text)
    text = _MARKUP.sub("", text)
    return _SPACES.sub(" ", text).strip()


def estimate_speech_seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND + 0.3


class SpeechSegmenter:
    """Splits streamed LLM text into sentence-sized pieces."""

    def __init__(self, min_chars: int = 12, soft_limit: int = 220) -> None:
        self._buffer = ""
        self._min_chars = min_chars
        self._soft_limit = soft_limit

    def _boundary(self) -> int | None:
        for match in re.finditer(r"[.!?]+[\"')\]]?(?=\s)", self._buffer):
            end = match.end()
            if end < self._min_chars:
                continue
            word = re.search(r"([\w.]+)[.!?]*[\"')\]]?$", self._buffer[:end])
            if word and word.group(1).lower().rstrip(".") in ABBREVIATIONS and match.group(0) == ".":
                continue
            return end
        if len(self._buffer) > self._soft_limit:
            cut = max(self._buffer.rfind(", ", 0, self._soft_limit), self._buffer.rfind("; ", 0, self._soft_limit))
            if cut > self._min_chars:
                return cut + 1
            space = self._buffer.rfind(" ", 0, self._soft_limit)
            return space if space > 0 else self._soft_limit
        return None

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        segments: list[str] = []
        while (boundary := self._boundary()) is not None:
            segment = self._buffer[:boundary].strip()
            self._buffer = self._buffer[boundary:].lstrip()
            if segment:
                segments.append(segment)
        return segments

    def flush(self) -> list[str]:
        rest = self._buffer.strip()
        self._buffer = ""
        return [rest] if rest else []
