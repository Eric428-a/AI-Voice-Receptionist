"""Receptionist prompt loading and rendering."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings

PROMPT_DIRECTORY = Path(__file__).resolve().parents[2] / "knowledge" / "prompts"
RECEPTIONIST_PROMPT_FILE = PROMPT_DIRECTORY / "receptionist.txt"

NO_KNOWLEDGE_NOTE = (
    "No company documents matched this question. Do not state any company-specific "
    "facts. If the caller asked about the company, say you don't have verified "
    "information on that, and offer to help with something else."
)


class PromptLibrary:
    def __init__(self, settings: Settings, prompt_file: Path = RECEPTIONIST_PROMPT_FILE) -> None:
        self._settings = settings
        self._template = prompt_file.read_text(encoding="utf-8")

    def system_prompt(self, knowledge_context: str | None) -> str:
        base = self._settings.render_template(self._template).strip()
        knowledge = knowledge_context.strip() if knowledge_context else ""
        section = knowledge if knowledge else NO_KNOWLEDGE_NOTE
        return f"{base}\n\n## Retrieved company knowledge (internal - never read aloud)\n{section}"

    @property
    def greeting(self) -> str:
        return self._settings.greeting_text

    @property
    def unavailable_message(self) -> str:
        return (
            "I'm sorry, I'm having trouble reaching my information system right now. "
            "Please try again in a little while."
        )
