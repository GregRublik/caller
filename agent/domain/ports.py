"""Ports: interfaces for the external speech tools.

The infrastructure layer implements them; LiveKit adapters depend on the
interfaces, not on concrete clients.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from .models import AudioStream, Transcription


class STTPort(Protocol):
    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> Transcription: ...

    async def aclose(self) -> None: ...


class TTSPort(Protocol):
    def stream(self, text: str) -> AbstractAsyncContextManager[AudioStream]: ...

    async def aclose(self) -> None: ...
