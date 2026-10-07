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


@dataclass(frozen=True)
class MediaPart:
    mime_type: str  # e.g. "image/jpeg", "audio/mpeg"
    data: bytes


@dataclass(frozen=True)
class GeneratedMedia:
    parts: tuple[MediaPart, ...]
    text: str  # model text alongside the media: lyrics for music, a caption for images
