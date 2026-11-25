#!/usr/bin/env bash
# Native deployment of the LiveKit sales agent (agent only).
#
# LLM, STT and TTS are separate services launched elsewhere (see the
# sporbita/voice-agent project). This script installs only the agent process:
# its Python environment, the supervisor unit and the log directory.
#
# Run on the host as root:
#   /workspace/caller/deploy/agent-node/install.sh
#
# Assumes /workspace/caller is a copy of this repository and that
# /workspace/caller/.env exists (see env.agent.example).
set -euo pipefail

ROOT=${ROOT:-/workspace/caller}
VENV="$ROOT/venvs/agent"
PY="$VENV/bin/python"

echo "== free space before install"
df -h / | tail -1

mkdir -p "$VENV" "$ROOT/logs" "$ROOT/recordings"

echo "== agent venv"
uv venv --allow-existing --python 3.12 "$VENV"
uv pip install --python "$PY" -r "$ROOT/agent/requirements.txt"

echo "== supervisor unit"
install -m 644 "$ROOT/deploy/agent-node/supervisor/caller-agent.conf" \
  /etc/supervisor/conf.d/
supervisorctl reread
supervisorctl update

if [ ! -f "$ROOT/.env" ]; then
  echo "WARNING: $ROOT/.env is missing."
  echo "         Copy deploy/agent-node/env.agent.example to $ROOT/.env and fill it."
fi

echo "== done"
supervisorctl status caller-agent || true
