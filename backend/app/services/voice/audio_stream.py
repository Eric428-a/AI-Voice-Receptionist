"""Framing helpers for streamed PCM audio."""

from __future__ import annotations

import struct

from app.schemas.audio import AUDIO_PACKET_HEADER_BYTES, BYTES_PER_SAMPLE

_HEADER = struct.Struct("<I")


class PcmFrameAssembler:
    """Re-frames arbitrary incoming byte chunks into fixed-size PCM16 frames."""

    def __init__(self, sample_rate: int, frame_ms: int) -> None:
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.frame_bytes = int(sample_rate * frame_ms / 1000) * BYTES_PER_SAMPLE
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        frames: list[bytes] = []
        while len(self._buffer) >= self.frame_bytes:
            frames.append(bytes(self._buffer[: self.frame_bytes]))
            del self._buffer[: self.frame_bytes]
        return frames

    def reset(self) -> None:
        self._buffer.clear()


class Pcm16Aligner:
    """Keeps outgoing PCM chunks sample-aligned (providers may split mid-sample)."""

    def __init__(self) -> None:
        self._carry = b""

    def push(self, chunk: bytes) -> bytes:
        data = self._carry + chunk
        cut = len(data) - (len(data) % BYTES_PER_SAMPLE)
        self._carry = data[cut:]
        return data[:cut]


def encode_audio_packet(response_id: int, pcm: bytes) -> bytes:
    return _HEADER.pack(response_id & 0xFFFFFFFF) + pcm


def decode_audio_packet(packet: bytes) -> tuple[int, bytes]:
    if len(packet) < AUDIO_PACKET_HEADER_BYTES:
        raise ValueError("audio packet too short")
    (response_id,) = _HEADER.unpack_from(packet, 0)
    return response_id, packet[AUDIO_PACKET_HEADER_BYTES:]
