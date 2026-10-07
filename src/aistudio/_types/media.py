"""Media generation result types (spec §6.4)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechAudio:
    mime_type: str  # e.g. "audio/l16; rate=24000; channels=1"
    rate: int
    channels: int
    pcm: bytes  # 16-bit little-endian samples

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (self.rate * self.channels * 2)
