"""LiveKit STT plugin backed by the local GigaAM-v3 service.

The service exposes a batch endpoint: it accepts raw PCM16LE mono audio and
returns a final transcript. LiveKit's ``StreamAdapter`` wraps this plugin and
uses the shared Silero VAD to cut the microphone stream into utterances.
"""

from __future__ import annotations

import httpx
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    stt,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given


class GigaAMSTT(stt.STT):
    SAMPLE_RATE = 16000  # GigaAM-v3 is trained on 16 kHz audio

    def __init__(
        self,
        *,
        base_url: str,
        language: str = "ru",
        timeout: float = 30.0,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
                offline_recognize=True,
            )
        )
        self._base_url = base_url.rstrip("/")
        self._language = language
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=20),
        )

    @property
    def model(self) -> str:
        return "gigaam-v3"

    @property
    def provider(self) -> str:
        return "local-gigaam"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        frame = rtc.combine_audio_frames(buffer)
        if frame.sample_rate != self.SAMPLE_RATE:
            resampler = rtc.AudioResampler(
                input_rate=frame.sample_rate,
                output_rate=self.SAMPLE_RATE,
                num_channels=frame.num_channels,
                quality=rtc.AudioResamplerQuality.HIGH,
            )
            parts = list(resampler.push(frame))
            parts.extend(resampler.flush())
            if parts:
                frame = rtc.combine_audio_frames(parts)

        lang = language if is_given(language) else self._language

        try:
            response = await self._client.post(
                f"{self._base_url}/v1/transcribe",
                params={
                    "sample_rate": frame.sample_rate,
                    "channels": frame.num_channels,
                },
                content=frame.data.tobytes(),
                timeout=httpx.Timeout(self._timeout, connect=conn_options.timeout),
            )
        except httpx.TimeoutException as exc:
            raise APITimeoutError() from exc
        except httpx.HTTPError as exc:
            raise APIConnectionError() from exc

        if response.status_code != 200:
            raise APIStatusError(
                f"GigaAM STT returned {response.status_code}: {response.text[:500]}",
                status_code=response.status_code,
                request_id=response.headers.get("x-request-id"),
                body=response.text[:2000],
            )

        payload = response.json()
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    language=payload.get("language", lang),
                    text=(payload.get("text") or "").strip(),
                    confidence=1.0,
                )
            ],
        )

    async def aclose(self) -> None:
        await self._client.aclose()
