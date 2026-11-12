"""Audio format definitions shared by the WebSocket protocol.

Binary frames
-------------
* client -> server: raw little-endian signed 16-bit mono PCM at the negotiated
  input sample rate (``AUDIO_SAMPLE_RATE``, default 16 kHz).
* server -> client: a 4-byte little-endian unsigned ``response_id`` header
  followed by little-endian signed 16-bit mono PCM at the TTS sample rate.
  The header lets the browser drop stale audio after an interruption.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AUDIO_PACKET_HEADER_BYTES = 4
BYTES_PER_SAMPLE = 2


class AudioFormat(BaseModel):
    sample_rate: int = Field(ge=8000, le=48000)
    encoding: Literal["pcm16"] = "pcm16"
    channels: Literal[1] = 1
