"""WAV container for the raw PCM that AI Studio's TTS returns (spec §6.4)."""

from __future__ import annotations

import io
import wave


def pcm_to_wav(pcm: bytes, *, rate: int, channels: int = 1, sample_width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(sample_width)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()
