#!/usr/bin/env bash
# Native vLLM launch for hosts where Docker is not available (e.g. a vast.ai
# container). On a regular GPU host prefer docker-compose.yml.
#
# Requires vllm on PATH (pip install vllm) or VLLM_BIN pointing to it.
# Reads llm/.env by default; override with LLM_ENV_FILE.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ENV_FILE=${LLM_ENV_FILE:-"$HERE/.env"}

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

export HF_HOME=${HF_HOME:-"$HERE/.hf_home"}
VLLM_BIN=${VLLM_BIN:-vllm}

exec "$VLLM_BIN" serve "${LLM_MODEL_ID:-Qwen/Qwen3-14B-FP8}" \
  --host "${LLM_HOST:-127.0.0.1}" \
  --port "${LLM_PORT:-8000}" \
  --served-model-name "${LLM_MODEL:-qwen3-14b}" \
  --max-model-len "${LLM_MAX_MODEL_LEN:-8192}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.40}" \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --api-key "${VLLM_API_KEY:?VLLM_API_KEY is required}"
