"""Records the whole room into a single mixed WAV file.

Why not LiveKit Egress: its room-composite mode needs a headless Chrome, and
the VPS (~1 GB RAM) already runs the LiveKit server, SIP and Redis there. The
GPU node has plenty of headroom, and the agent process already has LiveKit
credentials, so the recorder simply joins the room as an extra *hidden*
participant, subscribes to every remote audio track (the caller's microphone
and the agent's own TTS) and mixes them in real time with numpy.

The file is a plain PCM16 mono WAV at 24 kHz, so it can be downloaded from
the GPU node and played anywhere (``scp`` from ``/workspace/caller/recordings``).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from livekit import api, rtc

logger = logging.getLogger("recorder")

SAMPLE_RATE = 24000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2
# Keep at most ~1 s of audio buffered per track. A burst (e.g. after a
# network hiccup) would otherwise shift that track into the future for the
# rest of the call; dropping the excess keeps all tracks near real time.
MAX_BUFFERED_BYTES = FRAME_BYTES * 50


class RoomRecorder:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
        room_name: str,
        recordings_dir: str,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._api_secret = api_secret
        self._room_name = room_name
        self._identity = f"recorder-{uuid.uuid4().hex[:6]}"

        directory = Path(recordings_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.path = directory / f"{room_name}-{stamp}.wav"

        self._room = rtc.Room()
        self._wav: wave.Wave_write | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._queues: list[asyncio.Queue[bytes]] = []
        self._closed = False

    async def start(self) -> None:
        self._wav = wave.open(str(self.path), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(SAMPLE_RATE)

        @self._room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track,
            publication: rtc.RemoteTrackPublication,
            participant: rtc.RemoteParticipant,
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            queue: asyncio.Queue[bytes] = asyncio.Queue()
            self._queues.append(queue)
            self._tasks.append(asyncio.create_task(self._pump_track(track, queue)))

        token = (
            api.AccessToken(self._api_key, self._api_secret)
            .with_identity(self._identity)
            .with_name("recorder")
            .with_grants(
                api.VideoGrants(room_join=True, room=self._room_name, hidden=True)
            )
            .to_jwt()
        )

        try:
            await self._room.connect(self._url, token)
        except Exception:
            self._finalize()
            raise

        self._tasks.append(asyncio.create_task(self._write_loop()))
        logger.info(
            "recorder joined %r as %s, writing %s",
            self._room_name,
            self._identity,
            self.path,
        )

    async def _pump_track(self, track: rtc.Track, queue: asyncio.Queue[bytes]) -> None:
        stream = rtc.AudioStream(
            track,
            sample_rate=SAMPLE_RATE,
            num_channels=1,
            frame_size_ms=FRAME_MS,
        )
        try:
            async for event in stream:
                queue.put_nowait(bytes(event.frame.data))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("recorder: audio stream for %s failed", track.sid)
        finally:
            try:
                await stream.aclose()
            except Exception:
                pass

    async def _write_loop(self) -> None:
        assert self._wav is not None
        buffers: list[bytearray] = []
        next_tick = time.monotonic()
        try:
            while not self._closed:
                next_tick += FRAME_MS / 1000.0

                # Refill per-track buffers from their queues.
                while len(buffers) < len(self._queues):
                    buffers.append(bytearray())
                for buf, queue in zip(buffers, self._queues):
                    while len(buf) < FRAME_BYTES and not queue.empty():
                        buf += queue.get_nowait()
                    if len(buf) > MAX_BUFFERED_BYTES:
                        del buf[: len(buf) - FRAME_BYTES]

                mixed = np.zeros(FRAME_SAMPLES, dtype=np.int32)
                for buf in buffers:
                    take = buf[:FRAME_BYTES]
                    del buf[: len(take)]
                    if take:
                        samples = np.frombuffer(bytes(take), dtype=np.int16)
                        mixed[: len(samples)] += samples

                pcm = np.clip(mixed, -32768, 32767).astype(np.int16)
                self._wav.writeframes(pcm.tobytes())

                await asyncio.sleep(max(0.0, next_tick - time.monotonic()))
        except asyncio.CancelledError:
            raise

    def _finalize(self) -> None:
        if self._wav is not None:
            try:
                self._wav.close()
            except Exception:
                logger.exception("recorder: failed to finalize %s", self.path)
            else:
                logger.info("recording saved: %s", self.path)
            self._wav = None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True

        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._finalize()

        try:
            await self._room.disconnect()
        except Exception:
            pass
