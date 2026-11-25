"""LiveKit TTS adapter over :class:`domain.ports.TTSPort`.

The adapter only bridges LiveKit's chunked synthesis API and the TTS port:
it requests audio, pushes the received PCM chunks into ``AudioEmitter`` and
maps service errors onto LiveKit API errors.
"""

from __future__ import annotations

import uuid

from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    tts,
)

from domain.ports import TTSPort
from infrastructure.clients.exceptions import (
    ServiceError,
    ServiceResponseError,
    ServiceTimeout,
)


class EspeechTTS(tts.TTS):
    def __init__(self, *, service: TTSPort, sample_rate: int = 24000) -> None:
        super().__init__(
            # The service takes a whole text and returns audio; it is not a
            # true streaming TTS. Declaring streaming=False makes AgentSession
            # wrap this plugin in tts.StreamAdapter automatically (the base
            # class only raises NotImplementedError for stream() if we lie).
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._service = service

    @property
    def model(self) -> str:
        return "espeech-tts"

    @property
    def provider(self) -> str:
        return "local-espeech"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "ChunkedStream":
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        await self._service.aclose()


class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: EspeechTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = uuid.uuid4().hex

        try:
            async with self._tts._service.stream(self.input_text) as audio:
                output_emitter.initialize(
                    request_id=request_id,
                    sample_rate=audio.sample_rate,
                    num_channels=audio.num_channels,
                    mime_type="audio/pcm",
                )
                async for chunk in audio.chunks:
                    output_emitter.push(chunk)

            output_emitter.flush()
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
