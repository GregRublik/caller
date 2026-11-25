#!/usr/bin/env bash
# LiveKit agent (supervisor wrapper). Needs a reachable LIVEKIT_URL and the
# same API key/secret as the LiveKit server; LLM/STT/TTS URLs come from .env.
set -euo pipefail
set -a
# shellcheck disable=SC1091
. /workspace/caller/.env
set +a

cd /workspace/caller/agent
exec /workspace/caller/venvs/agent/bin/python main.py start
