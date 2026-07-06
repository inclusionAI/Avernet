#!/usr/bin/env bash
# Minimal smoke test for first-round BCS migration.
# Verifies protocol / auth / WS boundaries survived the reshape.
#
# Requires: curl, jq, websocat (or equivalent ws client).
# BCS runs in local mode with no external service dependencies required.

set -euo pipefail

BCS_PORT="${BCS_PORT:-21000}"
BCS_URL="http://127.0.0.1:${BCS_PORT}"
BCS_DATA_DIR=$(mktemp -d)
export BCS_DATA_DIR
trap 'kill $BCS_PID 2>/dev/null || true; rm -rf "$BCS_DATA_DIR"' EXIT

MANIFEST_PATH="/Users/ray/ant/projects/ocb/src/bcs/Cargo.toml"

# 1. Start BCS in background
cargo run --package bcs --manifest-path "$MANIFEST_PATH" > /tmp/bcs-smoke.log 2>&1 &
BCS_PID=$!

# Wait for health endpoint (up to 60s)
for _ in $(seq 1 60); do
    if curl -sf "$BCS_URL/health" >/dev/null 2>&1; then break; fi
    sleep 1
done

if ! curl -sf "$BCS_URL/health" >/dev/null 2>&1; then
    echo "FAIL: BCS did not start within 60s. Log at /tmp/bcs-smoke.log"
    exit 1
fi

# 2. GET /health
echo "[1/7] health check"
curl -sf "$BCS_URL/health" >/dev/null || { echo "FAIL health"; exit 1; }

# 3. POST /bots/onboard (no token — new bot)
echo "[2/7] onboard new bot"
RESP=$(curl -sf -X POST "$BCS_URL/bots/onboard" \
    -H 'Content-Type: application/json' \
    -d '{"name":"smoke-bot","summary":"smoke test","skills":["debug"]}')
BOT_ID=$(echo "$RESP" | jq -r '.bot_id')
TOKEN=$(echo "$RESP" | jq -r '.token')
if [ -z "$BOT_ID" ] || [ "$BOT_ID" = "null" ]; then
    echo "FAIL onboard: $RESP"; exit 1
fi

# 4. WebSocket /ws/bot with valid token — send bot.connect, expect ok
if command -v websocat >/dev/null 2>&1; then
    echo "[3/7] ws/bot with valid token"
    RES=$(echo '{"type":"req","id":"1","method":"bot.connect","params":{"token":"'$TOKEN'"}}' | \
        websocat -n1 "ws://127.0.0.1:$BCS_PORT/ws/bot?token=$TOKEN" 2>/dev/null)
    if ! echo "$RES" | jq -e '.ok == true' >/dev/null 2>&1; then
        echo "FAIL ws.connect: $RES"; exit 1
    fi
else
    echo "[3/7] ws/bot with valid token (SKIPPED: websocat not installed)"
fi

# 5. WebSocket /ws/bot with invalid token — expect 401
echo "[4/7] ws/bot with invalid token (expect 401)"
STATUS=$(curl -s -o /dev/null -w '%{http_code}' \
    -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
    -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
    "$BCS_URL/ws/bot?token=invalid-garbage-xyz")
if [ "$STATUS" != "401" ]; then
    echo "FAIL ws auth reject: got $STATUS"; exit 1
fi

# 6. POST /bots/<id>/chat with valid Bearer — expect 2xx
echo "[5/7] bot chat with valid Bearer"
curl -sf -X POST "$BCS_URL/bots/$BOT_ID/chat" \
    -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"content":"hello","message_type":"text"}' >/dev/null || {
        echo "FAIL bot chat"; exit 1
    }

# 7. POST /bots/<id>/chat with forged Bearer — expect 401/403
echo "[6/7] bot chat with forged Bearer (expect 401/403)"
STATUS=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BCS_URL/bots/$BOT_ID/chat" \
    -H "Authorization: Bearer forged-token-abc" \
    -H 'Content-Type: application/json' \
    -d '{"content":"hello","message_type":"text"}')
if [ "$STATUS" != "401" ] && [ "$STATUS" != "403" ]; then
    echo "FAIL bot chat auth reject: got $STATUS"; exit 1
fi

# 8. bcs-cli health
echo "[7/7] bcs-cli health"
cargo run --package bcs-cli --manifest-path "$MANIFEST_PATH" -- \
    --url "$BCS_URL" health >/dev/null 2>&1 || {
        echo "FAIL bcs-cli health"; exit 1
    }

echo "PASS: all smoke checks"
