from __future__ import annotations

import asyncio
import json
import logging
import os

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RoomInputOptions,
    cli,
)
from livekit.agents import stt as lk_stt
from livekit.plugins import openai, silero

from local_plugins import GigaAMSTT, Qwen3TTS
from prompt import GREETING, SYSTEM_PROMPT

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sales-agent")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://llm:8000/v1")
LLM_API_KEY = os.getenv("VLLM_API_KEY", "EMPTY")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3-14b")

STT_BASE_URL = os.getenv("STT_BASE_URL", "http://stt:8001")
TTS_BASE_URL = os.getenv("TTS_BASE_URL", "http://tts:8002")
TTS_SPEAKER = os.getenv("TTS_SPEAKER", "Aiden")
TTS_LANGUAGE = os.getenv("TTS_LANGUAGE", "ru")

AGENT_NAME = os.getenv("AGENT_NAME", "sales-agent")
SIP_OUTBOUND_TRUNK_ID = os.getenv("SIP_OUTBOUND_TRUNK_ID", "").strip()
SIP_ANSWER_TIMEOUT = float(os.getenv("SIP_ANSWER_TIMEOUT", "60"))

server = AgentServer()


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def _metadata_phone(metadata: str | None) -> str | None:
    """Read the destination number from job metadata.

    ``dial.py`` dispatches the agent with ``{"phone": "+7..."}``. Plain
    numbers are accepted as well, which is handy for manual testing.
    """
    if not metadata:
        return None
    metadata = metadata.strip()
    try:
        payload = json.loads(metadata)
    except json.JSONDecodeError:
        return metadata if metadata.startswith("+") else None
    if isinstance(payload, dict):
        phone = payload.get("phone") or payload.get("call_to")
        return str(phone).strip() if phone else None
    return None


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    await ctx.connect()

    phone = _metadata_phone(ctx.job.metadata)
    if phone and SIP_OUTBOUND_TRUNK_ID:
        logger.info("dialing %s via trunk %s", _mask_phone(phone), SIP_OUTBOUND_TRUNK_ID)
        try:
            await ctx.add_sip_participant(
                call_to=phone,
                trunk_id=SIP_OUTBOUND_TRUNK_ID,
                participant_identity=f"sip-{phone.lstrip('+')}",
            )
        except Exception:
            logger.exception("failed to create outbound SIP call to %s", _mask_phone(phone))
            ctx.shutdown("outbound call failed")
            return
    elif phone:
        logger.warning(
            "job metadata contains %s, but SIP_OUTBOUND_TRUNK_ID is empty; "
            "waiting for a participant to join",
            _mask_phone(phone),
        )

    try:
        participant = await asyncio.wait_for(
            ctx.wait_for_participant(), timeout=SIP_ANSWER_TIMEOUT
        )
    except asyncio.TimeoutError:
        logger.warning("no participant joined within %.0fs", SIP_ANSWER_TIMEOUT)
        ctx.shutdown("no participant")
        return

    logger.info("participant joined: %s (%s)", participant.identity, participant.kind)

    # Two VAD instances: one drives the session's turn detection, the other
    # cuts the audio that goes into StreamAdapter. Sharing one instance would
    # make two consumers compete for the same stream.
    session_vad = silero.VAD.load()
    stt_vad = silero.VAD.load()

    session = AgentSession(
        vad=session_vad,
        stt=lk_stt.StreamAdapter(
            stt=GigaAMSTT(base_url=STT_BASE_URL, language="ru"),
            vad=stt_vad,
        ),
        llm=openai.LLM(
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            temperature=0.6,
            max_completion_tokens=300,
            parallel_tool_calls=False,
            # Qwen3 is a hybrid thinking model. Thinking would add seconds to
            # the first token; vLLM understands this chat-template flag.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        ),
        tts=Qwen3TTS(
            base_url=TTS_BASE_URL,
            voice=TTS_SPEAKER,
            language=TTS_LANGUAGE,
        ),
        min_endpointing_delay=0.4,
        max_endpointing_delay=2.5,
    )

    agent = Agent(instructions=SYSTEM_PROMPT)
    # SIP delivers G.711 at 8 kHz. RoomIO resamples the participant's audio
    # once to 16 kHz, which is what GigaAM expects, so the STT plugin does not
    # have to resample again in the common case.
    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(audio_sample_rate=16000),
    )

    # The greeting is spoken verbatim and added to the chat context, so the
    # LLM knows it has already greeted the customer.
    await session.say(GREETING, add_to_chat_ctx=True)
    logger.info("agent session is ready")


if __name__ == "__main__":
    cli.run_app(server)
