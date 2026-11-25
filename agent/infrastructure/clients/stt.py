"""STT service client: HTTP implementation of :class:`domain.ports.STTPort`."""

from __future__ import annotations

import httpx

from domain.models import Transcription
from infrastructure.clients.exceptions import (
    ServiceResponseError,
    ServiceTimeout,
    ServiceUnavailable,
)

SERVICE = "stt"


class STTService:
    """Batch speech recognition: one finished utterance in, text out."""

    def __init__(self, *, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        )

    async def transcribe(
        self,
        pcm: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> Transcription:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/transcribe",
                params={"sample_rate": sample_rate, "channels": channels},
                content=pcm,
            )
        except httpx.TimeoutException as exc:
            raise ServiceTimeout("STT request timed out", service=SERVICE) from exc
        except httpx.HTTPError as exc:
            raise ServiceUnavailable("STT service is unreachable", service=SERVICE) from exc

        if response.status_code != 200:
            raise ServiceResponseError(
                f"STT returned {response.status_code}",
                service=SERVICE,
                status_code=response.status_code,
                body=response.text[:2000],
            )

        payload = response.json()
        return Transcription(
            text=(payload.get("text") or "").strip(),
            language=payload.get("language") or "ru",
            sample_rate=payload.get("sample_rate"),
            duration=payload.get("duration"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
