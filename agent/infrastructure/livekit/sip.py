"""Outbound SIP helpers for the agent entrypoint."""

from __future__ import annotations

import json
import logging

from livekit.agents import JobContext

logger = logging.getLogger("sales-agent")


def mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def phone_from_metadata(metadata: str | None) -> str | None:
    """Read the destination number from job metadata.

    ``scripts/dial.py`` dispatches the agent with ``{"phone": "+7..."}``.
    Plain numbers are accepted as well, which is handy for manual testing.
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


async def start_outbound_call(
    ctx: JobContext,
    *,
    phone: str,
    trunk_id: str,
) -> None:
    """Create the outbound SIP leg; the agent's own participant plays into it."""
    logger.info("dialing %s via trunk %s", mask_phone(phone), trunk_id)
    await ctx.add_sip_participant(
        call_to=phone,
        trunk_id=trunk_id,
        participant_identity=f"sip-{phone.lstrip('+')}",
    )
