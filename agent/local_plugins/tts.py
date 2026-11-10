"""LiveKit TTS plugin backed by the local Qwen3-TTS service.

The service returns raw PCM16LE mono audio over a chunked HTTP response. The
``X-Sample-Rate`` header tells the emitter how to interpret the bytes.
"""

from __future__ import annotations

import uuid

import httpx
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    tts,
)


class Qwen3TTS(tts.TTS):
    def __init__(
        self,
        *,
        base_url: str,
        voice: str = "Aiden",
        language: str = "ru",
        sample_rate: int = 24000,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            # The service takes a whole text and returns audio; it is not a
            # true streaming TTS. Declaring streaming=False makes AgentSession
            # wrap this plugin in tts.StreamAdapter automatically (the base
            # class only raises NotImplementedError for stream() if we lie).
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._base_url = base_url.rstrip("/")
        self._voice = voice
        self._language = language
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        )

    @property
    def model(self) -> str:
        return "qwen3-tts"

    @property
    def provider(self) -> str:
        return "local-qwen3-tts"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "ChunkedStream":
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        await self._client.aclose()


class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: Qwen3TTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = uuid.uuid4().hex

        try:
            async with self._tts._client.stream(
                "POST",
                f"{self._tts._base_url}/v1/tts/stream",
                json={
                    "text": self.input_text,
                    "speaker": self._tts._voice,
                    "language": self._tts._language,
                },
                timeout=httpx.Timeout(
                    self._tts._timeout, connect=self._conn_options.timeout
                ),
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise APIStatusError(
                        f"Qwen3 TTS returned {response.status_code}: "
                        f"{body[:500].decode('utf-8', 'replace')}",
                        status_code=response.status_code,
                        request_id=response.headers.get("x-request-id"),
                        body=body[:2000].decode("utf-8", "replace"),
                    )

                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=int(
                        response.headers.get("x-sample-rate", self._tts.sample_rate)
                    ),
                    num_channels=int(response.headers.get("x-channels", "1")),
                    mime_type="audio/pcm",
                )

                async for chunk in response.aiter_bytes():
                    if chunk:
                        output_emitter.push(chunk)

            output_emitter.flush()
        except httpx.TimeoutException as exc:
            raise APITimeoutError() from exc
        except httpx.HTTPError as exc:
            raise APIConnectionError() from exc
