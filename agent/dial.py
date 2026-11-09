"""Start an outbound call.

Creates a room, dispatches the sales agent into it, and lets the agent create
the SIP participant itself (``JobContext.add_sip_participant``). Requires a
configured ``SIP_OUTBOUND_TRUNK_ID`` in the agent environment.

Usage:
    python dial.py +79991234567
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid

from livekit import api


async def dial(phone: str, room_name: str | None = None) -> None:
    url = os.environ.get("LIVEKIT_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    agent_name = os.environ.get("AGENT_NAME", "sales-agent")

    if not (url and api_key and api_secret):
        raise SystemExit("LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required")

    room_name = room_name or f"call-{uuid.uuid4().hex[:10]}"
    lk = api.LiveKitAPI(url, api_key, api_secret)
    try:
        await lk.room.create_room(api.CreateRoomRequest(name=room_name))
        dispatch = await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=agent_name,
                room=room_name,
                metadata=json.dumps({"phone": phone}),
            )
        )
        print(f"room:     {room_name}")
        print(f"dispatch: {dispatch.id}")
        print(f"agent:    {agent_name}")
    finally:
        await lk.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Place an outbound SIP call")
    parser.add_argument("phone", help="destination number, e.g. +79991234567")
    parser.add_argument("--room", help="room name (default: random)")
    args = parser.parse_args()
    asyncio.run(dial(args.phone, args.room))


if __name__ == "__main__":
    main()
