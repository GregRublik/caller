"""TTS service client: HTTP implementation of :class:`domain.ports.TTSPort`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from domain.models import AudioStream
from infrastructure.clients.exceptions import (
    ServiceResponseError,
    ServiceTimeout,
    ServiceUnavailable,
)

SERVICE = "tts"


class TTSService:
    """Sentence-level synthesis: text in, chunked PCM16 mono out."""

    def __init__(self, *, base_url: str, timeout: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        )

    @asynccontextmanager
    async def stream(self, text: str) -> AsyncIterator[AudioStream]:
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/v1/tts/stream",
                json={"text": text},
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise ServiceResponseError(
                        f"TTS returned {response.status_code}",
                        service=SERVICE,
                        status_code=response.status_code,
                        body=body[:2000].decode("utf-8", "replace"),
                    )

                async def chunks() -> AsyncIterator[bytes]:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            yield chunk

                yield AudioStream(
                    sample_rate=int(response.headers.get("x-sample-rate", "24000")),
                    num_channels=int(response.headers.get("x-channels", "1")),
                    chunks=chunks(),
                )
        except httpx.TimeoutException as exc:
            raise ServiceTimeout("TTS request timed out", service=SERVICE) from exc
        except httpx.HTTPError as exc:
            raise ServiceUnavailable("TTS service is unreachable", service=SERVICE) from exc

    async def aclose(self) -> None:
        await self._client.aclose()
