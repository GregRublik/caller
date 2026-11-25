#!/usr/bin/env bash
# Supervisor wrapper for the LLM service (implementation lives in llm/serve.sh).
set -euo pipefail
exec /workspace/caller/llm/serve.sh
