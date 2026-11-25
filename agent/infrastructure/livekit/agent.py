"""LiveKit agent that keeps quiet while the scripted opening plays."""

from __future__ import annotations

import logging

from livekit.agents import Agent, StopResponse

from application import ScriptedPhase

logger = logging.getLogger("sales-agent")


class IntroAgent(Agent):
    """Agent that stays quiet while the scripted opening runs.

    While the scripted phase is active, the automatic "reply after user turn"
    is suppressed with ``StopResponse``: the client's answer belongs to the
    scripted flow (connection check → intro), not to the LLM. Once the intro
    has played out, the phase is finished and every following turn goes to the
    LLM.
    """

    def __init__(self, *, instructions: str, phase: ScriptedPhase) -> None:
        super().__init__(instructions=instructions)
        self._phase = phase

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        if self._phase.active:
            logger.info(
                "scripted phase: suppressed LLM reply to %r", new_message.text_content
            )
            raise StopResponse()
