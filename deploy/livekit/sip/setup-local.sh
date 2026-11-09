#!/usr/bin/env bash
# Creates the local test trunks and dispatch rules through the LiveKit CLI.
#
# Usage (from this directory):
#   ./setup-local.sh            # inbound trunk + dispatch rule without an agent
#   ./setup-local.sh --agent    # additionally the sales-agent dispatch rule
#   ./setup-local.sh --outbound # additionally the outbound trunk to Asterisk
#
# Defaults match docker-compose.local.yml (devkey/secret).
set -euo pipefail

LK_IMAGE=${LK_IMAGE:-livekit/livekit-cli:v2.19.3}
URL=${LIVEKIT_URL:-http://127.0.0.1:7880}
KEY=${LIVEKIT_API_KEY:-devkey}
SECRET=${LIVEKIT_API_SECRET:-secret}
DIR=$(cd "$(dirname "$0")" && pwd)

run_lk() {
  docker run --rm --network host -v "$DIR:/sip" "$LK_IMAGE" \
    --url "$URL" --api-key "$KEY" --api-secret "$SECRET" "$@"
}

echo "== inbound trunk"
run_lk sip inbound create /sip/inbound-local.json

echo "== dispatch rule (room only)"
run_lk sip dispatch create /sip/dispatch-test.json

for arg in "$@"; do
  case "$arg" in
    --agent)
      echo "== dispatch rule (sales-agent)"
      run_lk sip dispatch create /sip/dispatch-agent.json
      ;;
    --outbound)
      echo "== outbound trunk (Asterisk)"
      run_lk sip outbound create /sip/outbound-asterisk.json
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

echo
echo "done. list them with:"
echo "  docker run --rm --network host $LK_IMAGE --url $URL --api-key $KEY --api-secret $SECRET sip inbound list"
echo "  docker run --rm --network host $LK_IMAGE --url $URL --api-key $KEY --api-secret $SECRET sip dispatch list"
echo "  docker run --rm --network host $LK_IMAGE --url $URL --api-key $KEY --api-secret $SECRET sip outbound list"
