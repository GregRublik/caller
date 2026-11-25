"""LiveKit STT adapter over :class:`domain.ports.STTPort`.

The adapter only translates between LiveKit types and the STT port: it
combines audio frames, resamples to 16 kHz (what GigaAM expects), calls the
port and maps service errors onto LiveKit API errors.
"""

from __future__ import annotations

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

from domain.ports import STTPort
from infrastructure.clients.exceptions import (
    ServiceError,
    ServiceResponseError,
    ServiceTimeout,
)


class GigaAMSTT(stt.STT):
    SAMPLE_RATE = 16000  # GigaAM-v3 is trained on 16 kHz audio

    def __init__(self, *, service: STTPort, language: str = "ru") -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
                offline_recognize=True,
            )
        )
        self._service = service
        self._language = language

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
            result = await self._service.transcribe(
                frame.data.tobytes(),
                sample_rate=frame.sample_rate,
                channels=frame.num_channels,
            )
        except ServiceTimeout as exc:
            raise APITimeoutError() from exc
        except ServiceResponseError as exc:
            raise APIStatusError(
                str(exc),
                status_code=exc.status_code,
                body=exc.body,
            ) from exc
        except ServiceError as exc:
            raise APIConnectionError() from exc

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    language=result.language or lang,
                    text=result.text,
                    confidence=1.0,
                )
            ],
        )

    async def aclose(self) -> None:
        await self._service.aclose()
