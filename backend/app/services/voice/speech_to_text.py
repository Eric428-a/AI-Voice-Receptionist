"""Speech-to-text through OpenRouter.

``openrouter`` sends each caller utterance (WAV, base64) to an audio-input model
on OpenRouter's Chat Completions endpoint as an ``input_audio`` content part and
asks for a verbatim transcript. It uses the same model as the answers
(``OPENROUTER_MODEL``), which must accept audio input. ``disabled`` turns recognition
off and the call reports that clearly.
"""

from __future__ import annotations

import base64
import re
from abc import ABC, abstractmethod

from app.core.config import Settings
from app.core.errors import ProviderError, ProviderNotConfiguredError
from app.services.openrouter.client import OpenRouterClient
from app.utils.audio_utils import pcm16_duration_ms, pcm16_to_wav

MIN_AUDIO_MS = 250

# Phrases speech models commonly hallucinate on near-silent audio.
_HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching!", "thanks for watching.",
    "you", "bye.", ".", "subtitles by the amara.org community",
}
_TRAILING_NOISE = re.compile(r"^[\s.,!?-]+|[\s]+$")


class SpeechToTextError(ProviderError):
    code = "stt_failed"


def clean_transcript(text: str, duration_ms: int) -> str:
    cleaned = _TRAILING_NOISE.sub("", " ".join(text.split()))
    if duration_ms < 1200 and cleaned.lower() in _HALLUCINATIONS:
        return ""
    return cleaned


class SpeechToText(ABC):
    provider_name = "base"

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def _transcribe_wav(self, wav: bytes) -> str: ...

    async def transcribe(self, pcm16: bytes, sample_rate: int) -> str:
        duration = pcm16_duration_ms(len(pcm16), sample_rate)
        if duration < MIN_AUDIO_MS:
            return ""
        if not self.configured:
            raise ProviderNotConfiguredError("Speech recognition is not configured.", code="stt_unavailable")
        text = await self._transcribe_wav(pcm16_to_wav(pcm16, sample_rate))
        return clean_transcript(text, duration)

    async def aclose(self) -> None:  # noqa: B027
        return None


class DisabledSpeechToText(SpeechToText):
    provider_name = "disabled"

    @property
    def configured(self) -> bool:
        return False

    async def _transcribe_wav(self, wav: bytes) -> str:
        raise ProviderNotConfiguredError("Speech recognition is disabled.", code="stt_unavailable")


class OpenRouterSpeechToText(SpeechToText):
    provider_name = "openrouter"

    PROMPT = (
        "Transcribe this phone-call audio verbatim in its spoken language. "
        "Return only the transcript text. If there is no intelligible speech, return nothing."
    )

    def __init__(self, client: OpenRouterClient, model: str, language: str | None = None) -> None:
        self._client = client
        self._model = model.strip()
        self._prompt = self.PROMPT + (f" The caller most likely speaks language code '{language}'." if language else "")

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def configured(self) -> bool:
        return bool(self._model) and self._client.has_api_key

    async def _transcribe_wav(self, wav: bytes) -> str:
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self._prompt},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": base64.b64encode(wav).decode("ascii"), "format": "wav"},
                    },
                ],
            }
        ]
        try:
            return await self._client.complete(messages, temperature=0.0, max_tokens=400, model=self._model)
        except ProviderError as exc:
            raise SpeechToTextError(str(exc), status_code=exc.status_code) from exc


def create_speech_to_text(settings: Settings, openrouter: OpenRouterClient) -> SpeechToText:
    if settings.stt_provider == "openrouter":
        return OpenRouterSpeechToText(openrouter, settings.openrouter_model, settings.stt_language)
    return DisabledSpeechToText()