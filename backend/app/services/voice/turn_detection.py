"""Energy-based voice activity detection with adaptive noise floor.

Feeds fixed-size PCM16 frames and emits ``SpeechStarted`` / ``SpeechEnded``
events. While the assistant is audible, a stricter threshold and a longer
minimum speech duration are used so residual speaker echo (after the browser's
acoustic echo cancellation) is not mistaken for a caller barging in.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from app.core.config import Settings
from app.utils.audio_utils import rms_dbfs

NOISE_FLOOR_MIN_DB = -85.0
NOISE_FLOOR_MAX_DB = -40.0
NOISE_EMA_ALPHA = 0.05


@dataclass(frozen=True, slots=True)
class VadConfig:
    sample_rate: int = 16000
    frame_ms: int = 20
    speech_threshold_db: float = -45.0
    barge_in_threshold_db: float = -35.0
    noise_margin_db: float = 10.0
    min_speech_ms: int = 160
    barge_in_min_speech_ms: int = 320
    silence_ms: int = 750
    preroll_ms: int = 300
    max_utterance_seconds: float = 30.0

    @classmethod
    def from_settings(cls, settings: Settings) -> "VadConfig":
        return cls(
            sample_rate=settings.audio_sample_rate,
            frame_ms=settings.audio_frame_ms,
            speech_threshold_db=settings.vad_speech_threshold_db,
            barge_in_threshold_db=settings.vad_barge_in_threshold_db,
            noise_margin_db=settings.vad_noise_margin_db,
            min_speech_ms=settings.vad_min_speech_ms,
            barge_in_min_speech_ms=settings.vad_barge_in_min_speech_ms,
            silence_ms=settings.vad_silence_ms,
            preroll_ms=settings.vad_preroll_ms,
            max_utterance_seconds=settings.vad_max_utterance_seconds,
        )


@dataclass(frozen=True, slots=True)
class SpeechStarted:
    level_db: float
    during_assistant_audio: bool


@dataclass(frozen=True, slots=True)
class SpeechEnded:
    audio: bytes
    duration_ms: int
    truncated: bool


VadEvent = SpeechStarted | SpeechEnded


class VoiceActivityDetector:
    def __init__(self, config: VadConfig) -> None:
        self.config = config
        frames_preroll = (config.preroll_ms + config.barge_in_min_speech_ms) // config.frame_ms + 1
        self._preroll: deque[bytes] = deque(maxlen=max(1, frames_preroll))
        self._utterance = bytearray()
        self._in_speech = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._utterance_ms = 0
        self.noise_floor_db = -70.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._preroll.clear()
        self._utterance.clear()
        self._in_speech = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._utterance_ms = 0

    def threshold(self, assistant_audible: bool) -> float:
        adaptive = self.noise_floor_db + self.config.noise_margin_db
        base = max(self.config.speech_threshold_db, adaptive)
        if assistant_audible:
            return max(base, self.config.barge_in_threshold_db)
        return base

    def _update_noise_floor(self, level: float) -> None:
        updated = (1 - NOISE_EMA_ALPHA) * self.noise_floor_db + NOISE_EMA_ALPHA * level
        self.noise_floor_db = min(NOISE_FLOOR_MAX_DB, max(NOISE_FLOOR_MIN_DB, updated))

    def process_frame(self, frame: bytes, assistant_audible: bool = False) -> list[VadEvent]:
        cfg = self.config
        level = rms_dbfs(frame)
        events: list[VadEvent] = []

        if not self._in_speech:
            self._preroll.append(frame)
            if level >= self.threshold(assistant_audible):
                self._voiced_ms += cfg.frame_ms
            else:
                # Decay rather than reset so brief dips inside a word don't restart detection.
                self._voiced_ms = max(0, self._voiced_ms - cfg.frame_ms)
                if not assistant_audible:
                    self._update_noise_floor(level)
            needed = cfg.barge_in_min_speech_ms if assistant_audible else cfg.min_speech_ms
            if self._voiced_ms >= needed:
                self._in_speech = True
                self._utterance = bytearray(b"".join(self._preroll))
                self._utterance_ms = len(self._preroll) * cfg.frame_ms
                self._preroll.clear()
                self._silence_ms = 0
                events.append(SpeechStarted(level_db=level, during_assistant_audio=assistant_audible))
            return events

        self._utterance.extend(frame)
        self._utterance_ms += cfg.frame_ms
        if level >= self.threshold(False):
            self._silence_ms = 0
        else:
            self._silence_ms += cfg.frame_ms
        truncated = self._utterance_ms >= cfg.max_utterance_seconds * 1000
        if self._silence_ms >= cfg.silence_ms or truncated:
            events.append(
                SpeechEnded(audio=bytes(self._utterance), duration_ms=self._utterance_ms, truncated=truncated)
            )
            self.reset()
        return events
