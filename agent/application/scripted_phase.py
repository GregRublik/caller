"""Scripted-phase policy for the outbound scenario.

The opening (connection check + intro) is fully scripted: it is spoken through
TTS directly, and the LLM must not answer the client's turns while it plays.
This object holds that state; the LiveKit adapter only asks it whether replies
are currently suppressed.
"""

from __future__ import annotations


class ScriptedPhase:
    def __init__(self) -> None:
        self._active = True

    @property
    def active(self) -> bool:
        return self._active

    def finish(self) -> None:
        self._active = False
