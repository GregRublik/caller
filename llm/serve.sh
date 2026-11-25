#!/usr/bin/env bash
# LLM service: vLLM OpenAI-compatible server for the caller agent.
#
# Reads settings from /workspace/caller/.env (see deploy/gpu-node/env.gpu.example)
# and listens on 127.0.0.1:8000. The agent talks to it over HTTP only — this
# process is independent of the agent and is managed by supervisor as
# `caller-llm` (autorestart).
set -euo pipefail
set -a
# shellcheck disable=SC1091
. /workspace/caller/.env
set +a

export HF_HOME="${HF_HOME:-/workspace/.hf_home}"

exec /workspace/caller/venvs/llm/bin/vllm serve "${LLM_MODEL_ID:-Qwen/Qwen3-14B-FP8}" \
  --host 127.0.0.1 \
  --port 8000 \
  --served-model-name "${LLM_MODEL:-qwen3-14b}" \
  --max-model-len 8192 \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION:-0.40}" \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --api-key "${VLLM_API_KEY:?VLLM_API_KEY is required}"
