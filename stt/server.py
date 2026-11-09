"""Local GigaAM-v3 STT service.

Protocol (used by ``agent/local_plugins/stt.py``):

    POST /v1/transcribe?sample_rate=16000&channels=1
    body: raw PCM16LE
    -> {"text": "...", "language": "ru"}

    POST /v1/transcribe/wav
    body: WAV file (16-bit PCM)
    -> {"text": "...", "language": "ru"}

The model is loaded once at startup and kept on the GPU. Inference is
serialized with a lock: one GPU process handles one utterance at a time.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import torch
from fastapi import FastAPI, HTTPException, Query, Request
from transformers import AutoModel

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("gigaam-stt")

MODEL_ID = os.getenv("STT_MODEL_ID", "ai-sage/GigaAM-v3")
MODEL_REVISION = os.getenv("STT_MODEL_REVISION", "e2e_rnnt").strip()
DEVICE = os.getenv("DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
MAX_AUDIO_SECONDS = float(os.getenv("STT_MAX_AUDIO_SECONDS", "60"))
SHORT_FORM_SECONDS = 24.0
CHUNK_SECONDS = 20.0


def _downmix(pcm: bytes, channels: int) -> bytes:
    if channels <= 1:
        return pcm
    usable = len(pcm) - (len(pcm) % (2 * channels))
    samples = np.frombuffer(pcm[:usable], dtype=np.int16).reshape(-1, channels)
    mixed = samples.astype(np.float32).mean(axis=1)
    return np.clip(mixed, -32768, 32767).astype(np.int16).tobytes()


def _wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


class GigaAMEngine:
    def __init__(self) -> None:
        self._model = None
        self._lock = asyncio.Lock()
        self.ready = False

    def load(self) -> None:
        logger.info("loading %s revision=%s on %s", MODEL_ID, MODEL_REVISION or "main", DEVICE)
        kwargs = {"trust_remote_code": True}
        if MODEL_REVISION:
            kwargs["revision"] = MODEL_REVISION
        model = AutoModel.from_pretrained(MODEL_ID, **kwargs)
        self._model = model.to(DEVICE).eval()
        self.ready = True
        logger.info("GigaAM loaded")

    def warmup(self) -> None:
        try:
            silence = b"\x00\x00" * 16000  # one second at 16 kHz
            self._transcribe_pcm_sync(silence, 16000, 1)
            logger.info("GigaAM warmup done")
        except Exception:
            logger.exception("GigaAM warmup failed (service will still start)")

    async def transcribe(self, pcm: bytes, sample_rate: int, channels: int) -> str:
        async with self._lock:
            return await asyncio.to_thread(
                self._transcribe_pcm_sync, pcm, sample_rate, channels
            )

    def _transcribe_pcm_sync(self, pcm: bytes, sample_rate: int, channels: int) -> str:
        if self._model is None:
            raise RuntimeError("model is not loaded")

        pcm = _downmix(pcm, channels)
        max_bytes = int(SHORT_FORM_SECONDS * sample_rate) * 2
        if len(pcm) <= max_bytes:
            return self._transcribe_chunk(pcm, sample_rate)

        # GigaAM's short-form transcribe is limited to 25 s. For longer
        # utterances split into 20 s chunks and concatenate. Boundaries may
        # cut a word; in practice call utterances are much shorter.
        chunk_bytes = int(CHUNK_SECONDS * sample_rate) * 2
        parts: list[str] = []
        for offset in range(0, len(pcm), chunk_bytes):
            text = self._transcribe_chunk(pcm[offset : offset + chunk_bytes], sample_rate)
            if text:
                parts.append(text)
        return " ".join(parts)

    def _transcribe_chunk(self, pcm: bytes, sample_rate: int) -> str:
        path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(_wav_bytes(pcm, sample_rate))
                path = Path(tmp.name)
            return (self._model.transcribe(str(path)) or "").strip()
        finally:
            if path is not None:
                path.unlink(missing_ok=True)


engine = GigaAMEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(engine.load)
    await asyncio.to_thread(engine.warmup)
    yield


app = FastAPI(title="GigaAM-v3 STT", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "ok" if engine.ready else "loading",
        "model": MODEL_ID,
        "revision": MODEL_REVISION,
        "device": DEVICE,
    }


@app.post("/v1/transcribe")
async def transcribe(
    request: Request,
    sample_rate: int = Query(16000, ge=8000, le=48000),
    channels: int = Query(1, ge=1, le=2),
):
    body = await request.body()
    if len(body) < 2:
        raise HTTPException(status_code=400, detail="empty audio")
    if len(body) % 2:
        body = body[:-1]
    max_bytes = int(MAX_AUDIO_SECONDS * sample_rate * channels) * 2
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"audio is longer than {MAX_AUDIO_SECONDS:.0f} s",
        )

    text = await engine.transcribe(body, sample_rate, channels)
    return {
        "text": text,
        "language": "ru",
        "sample_rate": sample_rate,
        "duration": len(body) / (2 * channels * sample_rate),
    }


@app.post("/v1/transcribe/wav")
async def transcribe_wav(request: Request):
    body = await request.body()
    try:
        with wave.open(io.BytesIO(body), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            if wav.getsampwidth() != 2:
                raise HTTPException(status_code=400, detail="only 16-bit PCM WAV is supported")
            pcm = wav.readframes(wav.getnframes())
    except wave.Error as exc:
        raise HTTPException(status_code=400, detail=f"invalid WAV: {exc}") from exc

    text = await engine.transcribe(pcm, sample_rate, channels)
    return {"text": text, "language": "ru", "sample_rate": sample_rate}
