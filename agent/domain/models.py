"""Domain models shared by the service clients and LiveKit adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class Transcription:
    """Result of speech recognition for one utterance."""

    text: str
    language: str
    sample_rate: int | None = None
    duration: float | None = None


@dataclass(frozen=True)
class AudioStream:
    """Chunked PCM16 audio returned by the TTS service."""

    sample_rate: int
    num_channels: int
    chunks: AsyncIterator[bytes]
