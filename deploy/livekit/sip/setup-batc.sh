#!/usr/bin/env bash
# Creates the production trunks for the Exolve BATC path through the LiveKit CLI.
#
# Run on the LiveKit VPS from this directory:
#   LIVEKIT_URL=http://127.0.0.1:7880 \
#   LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=... ./setup-batc.sh
#
# Asterisk and LiveKit SIP must share the host network, so the trunk points at
# 127.0.0.1:5062 and the inbound trunk accepts calls from 127.0.0.1.
set -euo pipefail

LK_IMAGE=${LK_IMAGE:-livekit/livekit-cli:v2.19.3}
URL=${LIVEKIT_URL:-http://127.0.0.1:7880}
KEY=${LIVEKIT_API_KEY:?LIVEKIT_API_KEY is required}
SECRET=${LIVEKIT_API_SECRET:?LIVEKIT_API_SECRET is required}
DIR=$(cd "$(dirname "$0")" && pwd)

run_lk() {
  docker run --rm --network host -v "$DIR:/sip" "$LK_IMAGE" \
    --url "$URL" --api-key "$KEY" --api-secret "$SECRET" "$@"
}

echo "== inbound trunk (Asterisk -> LiveKit)"
run_lk sip inbound create /sip/inbound-local.json

echo "== dispatch rule (sales-agent)"
run_lk sip dispatch create /sip/dispatch-agent.json

echo "== outbound trunk (LiveKit -> Asterisk -> BATC)"
run_lk sip outbound create /sip/outbound-batc.json

echo
echo "Copy the outbound trunk ID (ST_...) into the agent .env as SIP_OUTBOUND_TRUNK_ID."
