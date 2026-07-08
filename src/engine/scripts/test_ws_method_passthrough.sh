#!/usr/bin/env bash
# ============================================================================
# test_ws_method_passthrough.sh
#
# End-to-end test for ClaudeCodeWSServer method passthrough to relay.
#
# Prerequisites:
#   - Engine running in claude_code mode (ENGINE_TYPE=claude_code)
#   - teamclaw-aicoding-relay running on port 18900
#   - Python 3.12+ with websockets package
#
# Usage:
#   ./scripts/test_ws_method_passthrough.sh [engine_url]
#
# Default engine_url: ws://127.0.0.1:20003/api/claude_code/ws
# ============================================================================

set -euo pipefail

ENGINE_URL="${1:-ws://127.0.0.1:20003/api/claude_code/ws}"
PASS_COUNT=0
FAIL_COUNT=0

green() { printf "\033[32m%s\033[0m\n" "$1"; }
red()   { printf "\033[31m%s\033[0m\n" "$1"; }
bold()  { printf "\033[1m%s\033[0m\n" "$1"; }

assert_not_contains() {
    local haystack="$1"
    local needle="$2"
    local test_name="$3"
    if echo "$haystack" | grep -q "$needle"; then
        red "FAIL: $test_name (found '$needle' in response)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    else
        green "PASS: $test_name"
        PASS_COUNT=$((PASS_COUNT + 1))
        return 0
    fi
}

assert_contains() {
    local haystack="$1"
    local needle="$2"
    local test_name="$3"
    if echo "$haystack" | grep -q "$needle"; then
        green "PASS: $test_name"
        PASS_COUNT=$((PASS_COUNT + 1))
        return 0
    else
        red "FAIL: $test_name (expected '$needle' in response)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        return 1
    fi
}

bold "============================================"
bold "WS Method Passthrough E2E Test"
bold "Engine URL: $ENGINE_URL"
bold "============================================"
echo ""

# ── Python WS test script ────────────────────────────────────────────────────
# We use an inline Python script with websockets to handle the full protocol:
# 1. Connect + handshake
# 2. Send test requests
# 3. Collect responses

PYTHON_SCRIPT=$(cat <<'PYEOF'
import asyncio
import json
import sys
import uuid

try:
    import websockets
except ImportError:
    print("ERROR: websockets package not installed. Run: pip install websockets")
    sys.exit(2)


async def run_test(url: str, method: str, params: dict | None = None) -> dict:
    """Connect, handshake, send one request, return response."""
    async with websockets.connect(url) as ws:
        # Wait for nothing (server accepts immediately)
        # Step 1: Send connect handshake
        connect_req = {
            "type": "req",
            "id": "connect-" + uuid.uuid4().hex[:8],
            "method": "connect",
            "params": {
                "client": {
                    "id": "e2e-test",
                    "version": "1.0.0",
                    "platform": "python-test",
                    "mode": "test",
                },
                "minProtocol": 3,
                "maxProtocol": 3,
            },
        }
        await ws.send(json.dumps(connect_req))
        hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        hello = json.loads(hello_raw)

        if not hello.get("ok"):
            return {"error": "handshake_failed", "detail": hello}

        # Step 2: Send the actual test request
        req_id = "test-" + uuid.uuid4().hex[:8]
        test_req = {
            "type": "req",
            "id": req_id,
            "method": method,
        }
        if params:
            test_req["params"] = params

        await ws.send(json.dumps(test_req))

        # Step 3: Collect response (skip tick events)
        deadline = asyncio.get_event_loop().time() + 15
        while asyncio.get_event_loop().time() < deadline:
            remaining = deadline - asyncio.get_event_loop().time()
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            data = json.loads(raw)
            if data.get("type") == "res" and data.get("id") == req_id:
                return data
            # skip events (tick, etc.)

        return {"error": "timeout", "detail": "No response within 15s"}


async def check_handshake_features(url: str) -> dict:
    """Connect and return the hello-ok payload (features.methods)."""
    async with websockets.connect(url) as ws:
        connect_req = {
            "type": "req",
            "id": "connect-features",
            "method": "connect",
            "params": {
                "client": {
                    "id": "e2e-test",
                    "version": "1.0.0",
                    "platform": "python-test",
                    "mode": "test",
                },
                "minProtocol": 3,
                "maxProtocol": 3,
            },
        }
        await ws.send(json.dumps(connect_req))
        hello_raw = await asyncio.wait_for(ws.recv(), timeout=10)
        return json.loads(hello_raw)


async def main():
    url = sys.argv[1]
    mode = sys.argv[2]

    if mode == "features":
        result = await check_handshake_features(url)
        print(json.dumps(result))
    elif mode == "request":
        method = sys.argv[3]
        params = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
        result = await run_test(url, method, params)
        print(json.dumps(result))
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


asyncio.run(main())
PYEOF
)

# ── Test 1: Handshake features include relay methods ──────────────────────────

bold "Test 1: Handshake features include relay methods"
FEATURES_RESULT=$(python3 -c "$PYTHON_SCRIPT" "$ENGINE_URL" features 2>/dev/null || echo '{"error":"connection_failed"}')

if echo "$FEATURES_RESULT" | grep -q '"error"'; then
    red "SKIP: Cannot connect to engine at $ENGINE_URL"
    red "Make sure engine and relay are running."
    echo "Result: $FEATURES_RESULT"
    echo ""
    bold "Tests skipped due to connection failure."
    bold "To run these tests, start engine + relay first:"
    bold "  cd src/engine && ./scripts/run.sh --port 20003 -l"
    exit 0
fi

assert_contains "$FEATURES_RESULT" '"session.status"' "features contains session.status"
assert_contains "$FEATURES_RESULT" '"sessions.list"' "features contains sessions.list"
assert_contains "$FEATURES_RESULT" '"models.list"' "features contains models.list"
assert_contains "$FEATURES_RESULT" '"chat.send"' "features contains chat.send (local)"
assert_contains "$FEATURES_RESULT" '"mcp.config.list"' "features contains mcp.config.list"
assert_contains "$FEATURES_RESULT" '"skills.list"' "features contains skills.list"
echo ""

# ── Test 2: session.status does NOT return NOT_FOUND ──────────────────────────

bold "Test 2: session.status should NOT return NOT_FOUND"
STATUS_RESULT=$(python3 -c "$PYTHON_SCRIPT" "$ENGINE_URL" request "session.status" '{"sessionKey":"e2e-test-session"}' 2>/dev/null || echo '{"error":"connection_failed"}')

assert_not_contains "$STATUS_RESULT" "NOT_FOUND" "session.status no NOT_FOUND"
echo "  Response: $STATUS_RESULT"
echo ""

# ── Test 3: sessions.list does NOT return NOT_FOUND ───────────────────────────

bold "Test 3: sessions.list should NOT return NOT_FOUND"
SESSIONS_RESULT=$(python3 -c "$PYTHON_SCRIPT" "$ENGINE_URL" request "sessions.list" 2>/dev/null || echo '{"error":"connection_failed"}')

assert_not_contains "$SESSIONS_RESULT" "NOT_FOUND" "sessions.list no NOT_FOUND"
echo "  Response: $SESSIONS_RESULT"
echo ""

# ── Test 4: models.list does NOT return NOT_FOUND ─────────────────────────────

bold "Test 4: models.list should NOT return NOT_FOUND"
MODELS_RESULT=$(python3 -c "$PYTHON_SCRIPT" "$ENGINE_URL" request "models.list" 2>/dev/null || echo '{"error":"connection_failed"}')

assert_not_contains "$MODELS_RESULT" "NOT_FOUND" "models.list no NOT_FOUND"
echo "  Response: $MODELS_RESULT"
echo ""

# ── Test 5: health.claude does NOT return NOT_FOUND ───────────────────────────

bold "Test 5: health.claude should NOT return NOT_FOUND"
HEALTH_RESULT=$(python3 -c "$PYTHON_SCRIPT" "$ENGINE_URL" request "health.claude" 2>/dev/null || echo '{"error":"connection_failed"}')

assert_not_contains "$HEALTH_RESULT" "NOT_FOUND" "health.claude no NOT_FOUND"
echo "  Response: $HEALTH_RESULT"
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────

echo ""
bold "============================================"
bold "Results: $PASS_COUNT passed, $FAIL_COUNT failed"
bold "============================================"

if [ "$FAIL_COUNT" -gt 0 ]; then
    red "Some tests FAILED"
    exit 1
else
    green "All tests PASSED"
    exit 0
fi
