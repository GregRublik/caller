"""HTTP clients for the external speech tools (implement domain ports)."""

from .exceptions import (
    ServiceError,
    ServiceResponseError,
    ServiceTimeout,
    ServiceUnavailable,
)
from .stt import STTService
from .tts import TTSService

__all__ = [
    "STTService",
    "ServiceError",
    "ServiceResponseError",
    "ServiceTimeout",
    "ServiceUnavailable",
    "TTSService",
]
