#!/usr/bin/env bash
# GigaAM-v3 STT service (supervisor wrapper).
set -euo pipefail
set -a
# shellcheck disable=SC1091
. /workspace/caller/.env
set +a

export HF_HOME="${HF_HOME:-/workspace/.hf_home}"
export DEVICE="${DEVICE:-cuda}"

cd /workspace/caller/stt
exec /workspace/caller/venvs/app/bin/uvicorn server:app \
  --host 127.0.0.1 --port 8001 --workers 1
