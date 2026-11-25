"""Composition root and LiveKit worker entrypoint.

Wires the domain scenario to the infrastructure adapters:

    main.py (composition root)
       ├── infrastructure/   LiveKit adapters + HTTP clients + config
       │       └── application/  scripted-phase policy
       │               └── domain/  models, ports, scenario text
       └── livekit.plugins.openai.LLM (standard plugin)

Nothing in ``domain``/``application`` imports this module; dependencies point
inward.
"""

from __future__ import annotations

import asyncio
import logging

from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    RoomInputOptions,
    cli,
)
from livekit.agents import stt as lk_stt
from livekit.plugins import openai, silero

from application import ScriptedPhase
from domain.prompt import CONNECT_CHECK, CONNECT_RETRY, INTRO, SYSTEM_PROMPT
from infrastructure.clients import STTService, TTSService
from infrastructure.config import settings
from infrastructure.livekit import EspeechTTS, GigaAMSTT, IntroAgent, RoomRecorder
from infrastructure.livekit.session import say_and_wait_for_reply
from infrastructure.livekit.sip import (
    mask_phone,
    phone_from_metadata,
    start_outbound_call,
)

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sales-agent")

server = AgentServer()


@server.rtc_session(agent_name=settings.agent_name)
async def entrypoint(ctx: JobContext):
    await ctx.connect()

    phone = phone_from_metadata(ctx.job.metadata)
    if phone and settings.sip.outbound_trunk_id:
        try:
            await start_outbound_call(
                ctx, phone=phone, trunk_id=settings.sip.outbound_trunk_id
            )
        except Exception:
            logger.exception(
                "failed to create outbound SIP call to %s", mask_phone(phone)
            )
            ctx.shutdown("outbound call failed")
            return
    elif phone:
        logger.warning(
            "job metadata contains %s, but SIP_OUTBOUND_TRUNK_ID is empty; "
            "waiting for a participant to join",
            mask_phone(phone),
        )

    try:
        participant = await asyncio.wait_for(
            ctx.wait_for_participant(), timeout=settings.sip.answer_timeout
        )
    except asyncio.TimeoutError:
        logger.warning(
            "no participant joined within %.0fs", settings.sip.answer_timeout
        )
        ctx.shutdown("no participant")
        return

    logger.info("participant joined: %s (%s)", participant.identity, participant.kind)

    # Two VAD instances: one drives the session's turn detection, the other
    # cuts the audio that goes into StreamAdapter. Sharing one instance would
    # make two consumers compete for the same stream.
    session_vad = silero.VAD.load()
    stt_vad = silero.VAD.load()

    # Service layer: HTTP clients for the separately launched tools.
    stt_service = STTService(base_url=settings.stt.base_url)
    tts_service = TTSService(base_url=settings.tts.base_url)

    session = AgentSession(
        vad=session_vad,
        stt=lk_stt.StreamAdapter(
            stt=GigaAMSTT(service=stt_service, language="ru"),
            vad=stt_vad,
        ),
        llm=openai.LLM(
            model=settings.llm.model,
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key,
            temperature=0.6,
            max_completion_tokens=120,
            parallel_tool_calls=False,
            # Qwen3 is a hybrid thinking model. Thinking would add seconds to
            # the first token; vLLM understands this chat-template flag.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        ),
        tts=EspeechTTS(service=tts_service),
        min_endpointing_delay=0.4,
        max_endpointing_delay=2.5,
    )

    # Log every user/agent turn to the worker log (otherwise transcripts are
    # only visible in LiveKit Cloud or via a client).
    @session.on("conversation_item_added")
    def _log_chat_item(ev) -> None:
        item = ev.item
        logger.info("chat[%s]: %s", getattr(item, "role", "?"), item.text_content)

    # Lean scenario prompt (see domain/prompt.py): the agent only qualifies the
    # lead and hands it over to a human manager. Until the scripted opening is
    # done, LLM replies are suppressed by the scripted-phase policy.
    phase = ScriptedPhase()
    agent = IntroAgent(instructions=SYSTEM_PROMPT, phase=phase)
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
    if (
        settings.livekit.url
        and settings.livekit.api_key
        and settings.livekit.api_secret
    ):
        recorder = RoomRecorder(
            url=settings.livekit.url,
            api_key=settings.livekit.api_key,
            api_secret=settings.livekit.api_secret,
            room_name=ctx.room.name,
            recordings_dir=settings.recordings_dir,
        )
        try:
            await recorder.start()
        except Exception:
            logger.exception("failed to start room recording")
            recorder = None
    if recorder is not None:
        ctx.add_shutdown_callback(recorder.aclose)

    # Stop recording and end the job as soon as the call ends. The recorder
    # is itself a room participant, so without this the job would stay alive
    # (and keep writing silence) after the caller hangs up. Closing only the
    # session leaves the job (and its agent room participant) alive as a
    # silent "ghost" until the room is deleted — shut the job down explicitly.
    @session.on("close")
    def _on_session_close(_ev) -> None:
        if recorder is not None:
            asyncio.create_task(recorder.aclose())
        ctx.shutdown("session closed")

    # Outbound call: make sure the client actually hears us before the pitch.
    # Right after the SIP call is answered the media path is not always up
    # yet and the first words can be lost, so start with a short connection
    # check. Only once the client answers do we introduce ourselves.
    for attempt, line in enumerate((CONNECT_CHECK, CONNECT_RETRY)):
        if attempt:
            logger.info(
                "connect check: no answer within %.0fs, asking again",
                settings.connect_check_timeout,
            )
        reply = await say_and_wait_for_reply(
            session, line, timeout=settings.connect_check_timeout
        )
        if reply is not None:
            logger.info("connect check: client answered %r", reply)
            break
    else:
        logger.warning("connect check: no verbal answer, introducing ourselves anyway")

    # The client has just answered the connection check, so their speech tail
    # often still overlaps the start of the intro and barge-in would cut it
    # off after the first word. The intro is short and scripted; play it
    # without interruptions.
    await session.say(INTRO, allow_interruptions=False, add_to_chat_ctx=True)

    # The scripted part is over; the LLM now handles the conversation.
    phase.finish()


if __name__ == "__main__":
    cli.run_app(server)
