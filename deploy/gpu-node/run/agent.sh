#!/usr/bin/env bash
# LiveKit agent (supervisor wrapper). Needs a reachable LIVEKIT_URL and the
# same API key/secret as the LiveKit server.
set -euo pipefail
set -a
# shellcheck disable=SC1091
. /workspace/caller/.env
set +a

export HF_HOME="${HF_HOME:-/workspace/.hf_home}"
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8000/v1}"
export STT_BASE_URL="${STT_BASE_URL:-http://127.0.0.1:8001}"
export TTS_BASE_URL="${TTS_BASE_URL:-http://127.0.0.1:8002}"

cd /workspace/caller/agent
exec /workspace/caller/venvs/app/bin/python main.py start
