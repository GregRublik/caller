#!/usr/bin/env bash
# Fast start of the GPU stack after an instance resume (vast.ai stop/start).
# Environments, models and supervisor units must already be installed by
# install.sh. Brings up supervisord if it is not running, starts vLLM/STT/TTS
# and waits for their health endpoints. The agent is started only with
# --agent (it also needs a reachable LiveKit server in .env).
set -euo pipefail

ROOT=${ROOT:-/workspace/caller}
WAIT=${WAIT:-600}
AGENT=0
for arg in "$@"; do
  case "$arg" in
    --agent) AGENT=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# In a container supervisord is not started by an init system after stop/start.
ensure_supervisord() {
  local pid
  pid=$(supervisorctl pid 2>/dev/null || true)
  if [ -z "$pid" ] || [ "$pid" = "0" ]; then
    echo "== starting supervisord"
    supervisord -c /etc/supervisor/supervisord.conf
    sleep 1
  fi
}
ensure_supervisord
supervisorctl reread >/dev/null
supervisorctl update >/dev/null

echo "== starting services"
for svc in caller-llm caller-stt caller-tts; do
  supervisorctl start "$svc" >/dev/null 2>&1 || true
done
if [ "$AGENT" = "1" ]; then
  supervisorctl start caller-agent >/dev/null 2>&1 || true
fi

# wait_for URL NAME
wait_for() {
  local url=$1 name=$2 i=0 limit=$((WAIT * 2))
  until curl -fsS "$url" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge "$limit" ]; then
      echo "timeout waiting for $name" >&2
      return 1
    fi
    [ $((i % 20)) -eq 0 ] && echo "  still waiting for $name ($((i / 2))s)"
    sleep 0.5
  done
  echo "  $name: ok"
}

echo "== waiting for health (max ${WAIT}s)"
wait_for http://127.0.0.1:8000/health vllm
wait_for http://127.0.0.1:8001/health stt
wait_for http://127.0.0.1:8002/health tts

echo "== status"
supervisorctl status | grep 'caller-' || true
