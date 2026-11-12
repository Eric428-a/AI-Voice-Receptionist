"""PCM helpers: level measurement, WAV encoding, durations."""

from __future__ import annotations

import io
import math
import wave

import numpy as np

from app.schemas.audio import BYTES_PER_SAMPLE

SILENCE_DBFS = -100.0


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    usable = len(pcm) - (len(pcm) % BYTES_PER_SAMPLE)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32)
    return samples / 32768.0


def rms_dbfs(pcm: bytes) -> float:
    """Root-mean-square level of a PCM16 buffer in dBFS (0 = full scale)."""
    samples = pcm16_to_float(pcm)
    if samples.size == 0:
        return SILENCE_DBFS
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 1e-10:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20.0 * math.log10(rms))


def pcm16_duration_ms(num_bytes: int, sample_rate: int) -> int:
    return int(num_bytes / BYTES_PER_SAMPLE / sample_rate * 1000)


def pcm16_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
    usable = len(pcm) - (len(pcm) % BYTES_PER_SAMPLE)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(BYTES_PER_SAMPLE)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm[:usable])
    return buffer.getvalue()


def generate_tone_pcm16(
    frequency: float, duration_ms: int, sample_rate: int, amplitude: float = 0.3
) -> bytes:
    """Synthesize a sine tone (used by tests and diagnostics)."""
    count = int(sample_rate * duration_ms / 1000)
    t = np.arange(count, dtype=np.float64) / sample_rate
    wave_data = amplitude * np.sin(2 * math.pi * frequency * t)
    return (np.clip(wave_data, -1, 1) * 32767).astype("<i2").tobytes()


def generate_silence_pcm16(duration_ms: int, sample_rate: int) -> bytes:
    return b"\x00\x00" * int(sample_rate * duration_ms / 1000)
