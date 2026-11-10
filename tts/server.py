"""Local TTS service.

Protocol (used by ``agent/local_plugins/tts.py``):

    POST /v1/tts/stream
    {"text": "...", "speaker": "Aiden", "language": "ru"}
    -> chunked raw PCM16LE mono, headers X-Sample-Rate / X-Channels

Two backends are available via ``TTS_BACKEND``:

- ``qwen`` (default): Qwen3-TTS-12Hz-0.6B-CustomVoice. Best voice quality, but
  generation is blocking in qwen-tts 0.1.1 (no incremental codec decoding) and
  measures ~1.5x slower than real time on this GPU (the bottleneck is the
  per-frame Python decode loop, GPU sits at ~17%), which makes a live phone
  conversation feel laggy.
- ``silero``: Silero TTS v5 (ru). Much faster (well above real time on CPU)
  at the cost of a more robotic voice. Selected for low-latency calls.

The first byte arrives after the whole text fragment is synthesized; the
response is still chunked, and the agent plays audio as soon as the first
chunks arrive. Splitting text per sentence (LiveKit does this) keeps each
request short.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from contextlib import asynccontextmanager

import numpy as np
import torch
import torchaudio
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from qwen_tts import Qwen3TTSModel

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("qwen3-tts")

BACKEND = os.getenv("TTS_BACKEND", "qwen").strip().lower()
MODEL_ID = os.getenv("TTS_MODEL_ID", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")
DEVICE = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
DEVICE_MAP = os.getenv("TTS_DEVICE_MAP") or (
    "cuda:0" if DEVICE.startswith("cuda") else DEVICE
)
DTYPE = torch.bfloat16 if DEVICE.startswith("cuda") else torch.float32
ATTN_IMPLEMENTATION = os.getenv("TTS_ATTN_IMPLEMENTATION", "sdpa")
DEFAULT_SPEAKER = os.getenv("TTS_SPEAKER", "Aiden")
DEFAULT_LANGUAGE = os.getenv("TTS_LANGUAGE", "ru")
OUTPUT_SAMPLE_RATE = int(os.getenv("TTS_OUTPUT_SAMPLE_RATE", "24000"))
CHUNK_MS = int(os.getenv("TTS_CHUNK_MS", "100"))

# Voice stability. LiveKit synthesizes the reply sentence by sentence, so every
# request starts from scratch; with the model's default sampling (temperature
# 0.9, top_k 50) the timbre drifts from sentence to sentence. Greedy decoding
# (the default here) keeps the speaker's voice identical. If sampling is
# wanted, set TTS_DO_SAMPLE=1 and optionally TTS_SEED to pin the RNG.
DO_SAMPLE = os.getenv("TTS_DO_SAMPLE", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
TEMPERATURE = float(os.getenv("TTS_TEMPERATURE", "0.7"))
TOP_K = int(os.getenv("TTS_TOP_K", "20"))
TOP_P = float(os.getenv("TTS_TOP_P", "0.9"))
REPETITION_PENALTY = float(os.getenv("TTS_REPETITION_PENALTY", "1.05"))
_seed = os.getenv("TTS_SEED", "").strip()
SEED = int(_seed) if _seed else None

LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "de": "German",
    "fr": "French",
    "pt": "Portuguese",
    "es": "Spanish",
    "it": "Italian",
}


def _language_name(value: str | None) -> str:
    value = (value or DEFAULT_LANGUAGE).strip()
    return LANGUAGE_NAMES.get(value.lower(), value)


class Engine:
    def __init__(self) -> None:
        self._model: Qwen3TTSModel | None = None
        self._lock = asyncio.Lock()
        self.supported_speakers: list[str] = []
        self.supported_languages: list[str] = []
        self.default_speaker = DEFAULT_SPEAKER
        self.ready = False

    def load(self) -> None:
        logger.info(
            "loading %s on %s (dtype=%s, attn=%s)",
            MODEL_ID,
            DEVICE_MAP,
            DTYPE,
            ATTN_IMPLEMENTATION,
        )
        self._model = Qwen3TTSModel.from_pretrained(
            MODEL_ID,
            device_map=DEVICE_MAP,
            dtype=DTYPE,
            attn_implementation=ATTN_IMPLEMENTATION,
        )
        self.supported_speakers = self._model.get_supported_speakers() or []
        self.supported_languages = self._model.get_supported_languages() or []

        if self.supported_speakers:
            match = next(
                (
                    s
                    for s in self.supported_speakers
                    if s.lower() == DEFAULT_SPEAKER.lower()
                ),
                None,
            )
            if match is None:
                self.default_speaker = self.supported_speakers[0]
                logger.warning(
                    "speaker %r is not supported, falling back to %r",
                    DEFAULT_SPEAKER,
                    self.default_speaker,
                )
            else:
                self.default_speaker = match

        self.ready = True
        logger.info(
            "Qwen3-TTS loaded; speakers=%s languages=%s",
            self.supported_speakers,
            self.supported_languages,
        )

    def warmup(self) -> None:
        try:
            self._synthesize_sync("Проверка связи.", self.default_speaker, "Russian")
            logger.info("Qwen3-TTS warmup done")
        except Exception:
            logger.exception("Qwen3-TTS warmup failed (service will still start)")

    def check_speaker(self, speaker: str) -> str:
        if not self.supported_speakers:
            return speaker
        for item in self.supported_speakers:
            if item.lower() == speaker.lower():
                return item
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"unsupported speaker: {speaker}",
                "supported": self.supported_speakers,
            },
        )

    async def synthesize(
        self,
        text: str,
        speaker: str,
        language: str,
        instruct: str | None,
    ) -> bytes:
        speaker = self.check_speaker(speaker)
        async with self._lock:
            try:
                return await asyncio.to_thread(
                    self._synthesize_sync, text, speaker, language, instruct
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _synthesize_sync(
        self,
        text: str,
        speaker: str,
        language: str,
        instruct: str | None = None,
    ) -> bytes:
        if self._model is None:
            raise RuntimeError("model is not loaded")

        if SEED is not None:
            torch.manual_seed(SEED)

        gen_kwargs: dict[str, object] = {
            "do_sample": DO_SAMPLE,
            "repetition_penalty": REPETITION_PENALTY,
        }
        if DO_SAMPLE:
            gen_kwargs.update(temperature=TEMPERATURE, top_k=TOP_K, top_p=TOP_P)
        else:
            # Explicit neutral values: HF generate warns when these conflict
            # with do_sample=False.
            gen_kwargs.update(temperature=1.0, top_k=0, top_p=1.0)

        wavs, sample_rate = self._model.generate_custom_voice(
            text=text,
            speaker=speaker,
            language=language,
            instruct=instruct,
            **gen_kwargs,
        )
        waveform = torch.from_numpy(np.asarray(wavs[0], dtype=np.float32))
        if sample_rate != OUTPUT_SAMPLE_RATE:
            waveform = torchaudio.functional.resample(
                waveform, sample_rate, OUTPUT_SAMPLE_RATE
            )
        pcm = np.clip(waveform.numpy(), -1.0, 1.0)
        return (pcm * 32767.0).astype(np.int16).tobytes()


class SileroEngine:
    """Silero TTS v5 (Russian) backend: low latency, CPU-friendly.

    Same interface as :class:`Engine` so the HTTP layer does not care which
    backend is active.
    """

    SILERO_SPEAKERS = ["aidar", "baya", "kseniya", "eugene", "xenia"]

    def __init__(self) -> None:
        self._model = None
        self._lock = asyncio.Lock()
        self.supported_speakers: list[str] = []
        self.supported_languages = ["russian"]
        self.default_speaker = DEFAULT_SPEAKER
        self.source_sample_rate = 48000
        self.ready = False

    def load(self) -> None:
        logger.info("loading Silero TTS v5_ru via torch.hub (cpu)")
        self._model, _ = torch.hub.load(
            "snakers4/silero-models",
            "silero_tts",
            language="ru",
            speaker="v5_ru",
            trust_repo=True,
        )
        self.source_sample_rate = int(getattr(self._model, "sample_rate", 48000))
        hub_speakers = getattr(self._model, "speakers", None) or []
        self.supported_speakers = list(hub_speakers) or list(self.SILERO_SPEAKERS)

        match = next(
            (
                s
                for s in self.supported_speakers
                if s.lower() == DEFAULT_SPEAKER.lower()
            ),
            None,
        )
        if match is not None:
            self.default_speaker = match
        else:
            self.default_speaker = (
                "aidar"
                if "aidar" in self.supported_speakers
                else self.supported_speakers[0]
            )
            if DEFAULT_SPEAKER.lower() != self.default_speaker.lower():
                logger.warning(
                    "speaker %r is not supported by Silero, falling back to %r",
                    DEFAULT_SPEAKER,
                    self.default_speaker,
                )
        self.ready = True
        logger.info(
            "Silero TTS loaded; speakers=%s sample_rate=%d",
            self.supported_speakers,
            self.source_sample_rate,
        )

    def warmup(self) -> None:
        try:
            self._synthesize_sync("Проверка связи.", self.default_speaker)
            logger.info("Silero TTS warmup done")
        except Exception:
            logger.exception("Silero TTS warmup failed (service will still start)")

    def check_speaker(self, speaker: str) -> str:
        if not self.supported_speakers:
            return speaker
        for item in self.supported_speakers:
            if item.lower() == speaker.lower():
                return item
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"unsupported speaker: {speaker}",
                "supported": self.supported_speakers,
            },
        )

    async def synthesize(
        self,
        text: str,
        speaker: str,
        language: str,
        instruct: str | None,
    ) -> bytes:
        speaker = self.check_speaker(speaker)
        async with self._lock:
            return await asyncio.to_thread(self._synthesize_sync, text, speaker)

    def _synthesize_sync(self, text: str, speaker: str) -> bytes:
        if self._model is None:
            raise RuntimeError("model is not loaded")

        audio = self._model.apply_tts(
            text=text,
            speaker=speaker,
            sample_rate=self.source_sample_rate,
        )
        waveform = torch.as_tensor(audio, dtype=torch.float32).flatten()
        if self.source_sample_rate != OUTPUT_SAMPLE_RATE:
            waveform = torchaudio.functional.resample(
                waveform, self.source_sample_rate, OUTPUT_SAMPLE_RATE
            )
        pcm = np.clip(waveform.numpy(), -1.0, 1.0)
        return (pcm * 32767.0).astype(np.int16).tobytes()


engine = SileroEngine() if BACKEND == "silero" else Engine()


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    speaker: str | None = None
    language: str = DEFAULT_LANGUAGE
    instruct: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(engine.load)
    await asyncio.to_thread(engine.warmup)
    yield


app = FastAPI(title="Qwen3-TTS", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok" if engine.ready else "loading",
        "backend": BACKEND,
        "model": MODEL_ID if BACKEND == "qwen" else "silero/v5_ru",
        "device": DEVICE_MAP,
        "default_speaker": engine.default_speaker,
        "speakers": engine.supported_speakers,
        "languages": engine.supported_languages,
        "sample_rate": OUTPUT_SAMPLE_RATE,
    }


@app.post("/v1/tts/stream")
async def tts_stream(request: TTSRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")

    pcm = await engine.synthesize(
        text=text,
        speaker=request.speaker or engine.default_speaker,
        language=_language_name(request.language),
        instruct=request.instruct,
    )

    async def chunks():
        step = max(2, int(OUTPUT_SAMPLE_RATE * CHUNK_MS / 1000) * 2)
        for offset in range(0, len(pcm), step):
            yield pcm[offset : offset + step]
            await asyncio.sleep(0)

    return StreamingResponse(
        chunks(),
        media_type="audio/pcm",
        headers={
            "x-sample-rate": str(OUTPUT_SAMPLE_RATE),
            "x-channels": "1",
            "cache-control": "no-store",
        },
    )


@app.post("/v1/tts")
async def tts_wav(request: TTSRequest):
    """Non-streaming endpoint for auditioning voices with curl."""
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")

    pcm = await engine.synthesize(
        text=text,
        speaker=request.speaker or engine.default_speaker,
        language=_language_name(request.language),
        instruct=request.instruct,
    )

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(OUTPUT_SAMPLE_RATE)
        wav.writeframes(pcm)
    return Response(buffer.getvalue(), media_type="audio/wav")
