"""LiveKit session helpers for the scripted opening."""

from __future__ import annotations

import asyncio

from livekit.agents import AgentSession


async def say_and_wait_for_reply(
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
