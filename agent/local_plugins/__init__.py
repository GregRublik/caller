"""Thin LiveKit plugins that talk to the local STT/TTS HTTP services."""

from .stt import GigaAMSTT
from .tts import Qwen3TTS

__all__ = ["GigaAMSTT", "Qwen3TTS"]
