"""ESpeech-TTS service.

ESpeech-TTS-1_RL-V2 is a Russian fine-tune of the F5-TTS architecture: a
336M-parameter DiT flow-matching model that clones a voice from a short
reference recording. The service therefore needs a reference audio file and
its exact transcript (``TTS_REF_AUDIO`` / ``TTS_REF_TEXT``).

Russian stress marks are produced by RUAccent (``+`` before the stressed
vowel). The reference text is accented once at startup; the reference audio
is preprocessed and cached by f5-tts.

Protocol (used by ``agent/local_plugins/tts.py``):

    POST /v1/tts/stream
    {"text": "..."}
    -> chunked raw PCM16LE mono, headers X-Sample-Rate / X-Channels

    POST /v1/tts
    same body -> WAV file (for auditioning with curl)

The whole fragment is synthesized before the first byte is sent; LiveKit
splits replies into sentences, so fragments stay short.
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
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from f5_tts.infer.utils_infer import (
    infer_process,
    load_model,
    load_vocoder,
    preprocess_ref_audio_text,
)
from f5_tts.model import DiT
from pydantic import BaseModel, Field
from ruaccent import RUAccent

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("espeech-tts")

MODEL_REPO = os.getenv("TTS_MODEL_REPO", "ESpeech/ESpeech-TTS-1_RL-V2")
MODEL_FILE = os.getenv("TTS_MODEL_FILE", "espeech_tts_rlv2.pt")
VOCAB_FILE = os.getenv("TTS_VOCAB_FILE", "vocab.txt")
MODEL_PATH = os.getenv("TTS_MODEL_PATH", "").strip()
VOCAB_PATH = os.getenv("TTS_VOCAB_PATH", "").strip()

DEVICE = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")

# Voice reference: any 5-15 s clean recording of the agent voice plus its
# exact transcript. Without both, the service cannot synthesize.
REF_AUDIO = os.getenv("TTS_REF_AUDIO", "").strip()
REF_TEXT = os.getenv("TTS_REF_TEXT", "").strip()

# Inference knobs. nfe_step trades quality for speed (32 default; 16 is
# roughly twice as fast at a small quality cost).
NFE_STEP = int(os.getenv("TTS_NFE_STEP", "32"))
CFG_STRENGTH = float(os.getenv("TTS_CFG_STRENGTH", "2.0"))
SWAY_COEF = float(os.getenv("TTS_SWAY_COEF", "-1"))
SPEED = float(os.getenv("TTS_SPEED", "1.0"))
TARGET_RMS = float(os.getenv("TTS_TARGET_RMS", "0.1"))

# Chunk size of the streamed PCM response (does not affect synthesis).
CHUNK_MS = int(os.getenv("TTS_CHUNK_MS", "100"))

# ESpeech checkpoint architecture (as specified by its model card).
MODEL_CFG = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)


def _noop(*args, **kwargs) -> None:
    return None


class Engine:
    def __init__(self) -> None:
        self._model = None
        self._vocoder = None
        self._accentizer: RUAccent | None = None
        self._ref_audio: str | None = None
        self._ref_text: str | None = None
        self._lock = asyncio.Lock()
        self.ready = False
        self.last_error: str | None = None

    def _resolve_paths(self) -> tuple[str, str]:
        model_path, vocab_path = MODEL_PATH, VOCAB_PATH
        if not model_path or not vocab_path:
            from huggingface_hub import hf_hub_download

            if not model_path:
                model_path = hf_hub_download(MODEL_REPO, MODEL_FILE)
            if not vocab_path:
                vocab_path = hf_hub_download(MODEL_REPO, VOCAB_FILE)
        return model_path, vocab_path

    def _accent(self, text: str) -> str:
        if "+" in text:
            return text
        assert self._accentizer is not None
        return self._accentizer.process_all(text)

    def load(self) -> None:
        try:
            if not REF_AUDIO or not REF_TEXT:
                raise RuntimeError(
                    "TTS_REF_AUDIO and TTS_REF_TEXT must be set "
                    "(voice reference recording and its exact transcript)"
                )

            model_path, vocab_path = self._resolve_paths()
            logger.info(
                "loading ESpeech-TTS: model=%s vocab=%s device=%s nfe=%d",
                model_path,
                vocab_path,
                DEVICE,
                NFE_STEP,
            )
            self._model = load_model(
                DiT,
                MODEL_CFG,
                model_path,
                vocab_file=vocab_path,
                device=DEVICE,
            )
            self._vocoder = load_vocoder(device=DEVICE)

            logger.info("loading RUAccent")
            self._accentizer = RUAccent()
            self._accentizer.load(
                omograph_model_size="turbo3.1",
                use_dictionary=True,
                tiny_mode=False,
            )

            self._ref_audio, self._ref_text = preprocess_ref_audio_text(
                REF_AUDIO,
                self._accent(REF_TEXT),
                show_info=_noop,
            )
            self.ready = True
            logger.info("ESpeech-TTS ready; reference=%s", REF_AUDIO)
        except Exception as exc:  # keep the service up and report via /health
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("ESpeech-TTS init failed")

    def warmup(self) -> None:
        if not self.ready:
            return
        try:
            self._synthesize_sync("Проверка связи.")
            logger.info("ESpeech-TTS warmup done")
        except Exception:
            logger.exception("ESpeech-TTS warmup failed")

    async def synthesize(self, text: str) -> tuple[bytes, int]:
        if not self.ready:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "tts engine is not ready",
                    "detail": self.last_error,
                },
            )
        async with self._lock:
            return await asyncio.to_thread(self._synthesize_sync, text)

    def _synthesize_sync(self, text: str) -> tuple[bytes, int]:
        if self._model is None or self._vocoder is None:
            raise RuntimeError("model is not loaded")

        audio, sample_rate, _ = infer_process(
            self._ref_audio,
            self._ref_text,
            self._accent(text),
            self._model,
            self._vocoder,
            show_info=_noop,
            target_rms=TARGET_RMS,
            nfe_step=NFE_STEP,
            cfg_strength=CFG_STRENGTH,
            sway_sampling_coef=SWAY_COEF,
            speed=SPEED,
            device=DEVICE,
        )
        if audio is None:
            raise RuntimeError("no audio generated")

        waveform = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
        pcm = (waveform * 32767.0).astype(np.int16).tobytes()
        return pcm, int(sample_rate)


engine = Engine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(engine.load)
    await asyncio.to_thread(engine.warmup)
    yield


app = FastAPI(title="ESpeech-TTS", lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get("/health")
async def health():
    if engine.ready:
        status = "ok"
    elif engine.last_error:
        status = "error"
    else:
        status = "loading"
    return {
        "status": status,
        "backend": "espeech",
        "model": MODEL_REPO,
        "device": DEVICE,
        "nfe_step": NFE_STEP,
        "ref_audio": REF_AUDIO or None,
        "error": engine.last_error,
    }


@app.post("/v1/tts/stream")
async def tts_stream(request: TTSRequest):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")

    pcm, sample_rate = await engine.synthesize(text)

    async def chunks():
        step = max(2, int(sample_rate * CHUNK_MS / 1000) * 2)
        for offset in range(0, len(pcm), step):
            yield pcm[offset : offset + step]
            await asyncio.sleep(0)

    return StreamingResponse(
        chunks(),
        media_type="audio/pcm",
        headers={
            "x-sample-rate": str(sample_rate),
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

    pcm, sample_rate = await engine.synthesize(text)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return Response(buffer.getvalue(), media_type="audio/wav")
