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
    StopResponse,
    cli,
)
from livekit.agents import stt as lk_stt
from livekit.plugins import openai, silero

from local_plugins import GigaAMSTT, Qwen3TTS
from prompt import CONNECT_CHECK, CONNECT_RETRY, INTRO, SYSTEM_PROMPT
from recorder import RoomRecorder

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
# How long to wait for the client to answer the connection check before
# repeating it (and, after the second try, introducing ourselves anyway).
CONNECT_CHECK_TIMEOUT = float(os.getenv("CONNECT_CHECK_TIMEOUT", "8"))

# Room recording (see recorder.py). The agent worker already runs with these
# credentials; the recorder needs them to mint its own hidden-participant
# token for the same room.
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "/workspace/caller/recordings")

server = AgentServer()


class _IntroAgent(Agent):
    """Agent that stays quiet while the scripted opening runs.

    While ``scripted_phase`` is True, the automatic "reply after user turn"
    is suppressed with ``StopResponse``: the client's answer belongs to the
    scripted flow (connection check → intro), not to the LLM. Once the intro
    has played out, the flag flips and every following turn goes to the LLM.
    """

    def __init__(self, *, instructions: str) -> None:
        super().__init__(instructions=instructions)
        self.scripted_phase = True

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        if self.scripted_phase:
            raise StopResponse()


async def _say_and_wait_for_reply(
    session: AgentSession, text: str, timeout: float
) -> str | None:
    """Say ``text`` and wait for the client to answer.

    Returns the caller's first final transcript, or ``None`` if the client
    stayed silent for ``timeout`` seconds after the line finished playing.
    The listener is registered before anything is spoken, so an answer that
    interrupts the line cannot race the playback.
    """
    reply: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def _on_transcript(ev) -> None:
        if ev.is_final and ev.transcript.strip() and not reply.done():
            reply.set_result(ev.transcript)

    session.on("user_input_transcribed", _on_transcript)
    try:
        await session.say(text, add_to_chat_ctx=True)
        return await asyncio.wait_for(reply, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        session.off("user_input_transcribed", _on_transcript)


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
            max_completion_tokens=120,
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

    # Log every user/agent turn to the worker log (otherwise transcripts are
    # only visible in LiveKit Cloud or via a client).
    @session.on("conversation_item_added")
    def _log_chat_item(ev) -> None:
        item = ev.item
        logger.info("chat[%s]: %s", getattr(item, "role", "?"), item.text_content)

    # Lean scenario prompt (see prompt.py): the agent only qualifies the lead
    # and hands it over to a human manager. Until the scripted opening is
    # done, the agent suppresses automatic LLM replies (see _IntroAgent).
    agent = _IntroAgent(instructions=SYSTEM_PROMPT)
    # SIP delivers G.711 at 8 kHz. RoomIO resamples the participant's audio
    # once to 16 kHz, which is what GigaAM expects, so the STT plugin does not
    # have to resample again in the common case.
    await session.start(
        agent=agent,
        room=ctx.room,
        room_input_options=RoomInputOptions(audio_sample_rate=16000),
    )

    logger.info("agent session is ready")

    # Record the conversation (caller + agent) into one mixed WAV. Must start
    # after session.start(): the recorder joins the room as another
    # participant, and the session has to pick the real caller first.
    recorder = None
    if LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET:
        recorder = RoomRecorder(
            url=LIVEKIT_URL,
            api_key=LIVEKIT_API_KEY,
            api_secret=LIVEKIT_API_SECRET,
            room_name=ctx.room.name,
            recordings_dir=RECORDINGS_DIR,
        )
        try:
            await recorder.start()
        except Exception:
            logger.exception("failed to start room recording")
            recorder = None
    if recorder is not None:
        ctx.add_shutdown_callback(recorder.aclose)

        # Stop recording as soon as the call ends. The recorder is itself a
        # room participant, so without this the job would stay alive (and
        # keep writing silence) after the caller hangs up.
        @session.on("close")
        def _on_session_close(_ev) -> None:
            asyncio.create_task(recorder.aclose())

    # Outbound call: make sure the client actually hears us before the pitch.
    # Right after the SIP call is answered the media path is not always up
    # yet and the first words can be lost, so start with a short connection
    # check. Only once the client answers do we introduce ourselves.
    for attempt, line in enumerate((CONNECT_CHECK, CONNECT_RETRY)):
        if attempt:
            logger.info(
                "connect check: no answer within %.0fs, asking again",
                CONNECT_CHECK_TIMEOUT,
            )
        reply = await _say_and_wait_for_reply(
            session, line, timeout=CONNECT_CHECK_TIMEOUT
        )
        if reply is not None:
            logger.info("connect check: client answered %r", reply)
            break
    else:
        logger.warning("connect check: no verbal answer, introducing ourselves anyway")

    await session.say(INTRO, add_to_chat_ctx=True)

    # The scripted part is over; the LLM now handles the conversation. The
    # flag flips only after the intro has played out, so a client who
    # interrupts the intro with an answer does not also trigger a duplicate
    # LLM reply to the connect-check small talk.
    agent.scripted_phase = False


if __name__ == "__main__":
    cli.run_app(server)
