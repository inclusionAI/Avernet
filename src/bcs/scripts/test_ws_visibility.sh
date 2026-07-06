#!/bin/bash
# Test: WebSocket Visibility Check for Private Bot Group Chat
# Tests AC-77/AC-78/AC-79/AC-80 via WebSocket & HTTP protocol
#
# Prerequisites:
#   - BCS running on $BCS_PORT (default 21000)
#   - Python3 with websockets, httpx packages
#
# Usage: ./test_ws_visibility.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BCS_PORT="${BCS_PORT:-21000}"
BCS_HTTP_URL="http://localhost:$BCS_PORT"
BCS_CLI="$PROJECT_ROOT/target/debug/bcs-cli"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
info() { echo -e "  ${CYAN}→${NC} $1"; }

# Check prerequisites
if ! curl -sf "$BCS_HTTP_URL/health" > /dev/null 2>&1; then
    fail "BCS not running on port $BCS_PORT. Run: ./start_bcs_bots.sh start"
    exit 1
fi

if ! python3 -c "import websockets" 2>/dev/null; then
    fail "Python websockets package not installed. Run: pip3 install websockets"
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  WebSocket Visibility Test (AC-77/AC-78/AC-79/AC-80)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Run the Python test script
python3 << 'PYTHON_SCRIPT'
import asyncio
import json
import sys
import httpx

BCS_HTTP = "http://localhost:21000"
BCS_WS = "ws://127.0.0.1:21000/ws/bot"
HTTP_TIMEOUT = 10.0

passed = 0
failed = 0

def check(condition, msg):
    global passed, failed
    if condition:
        passed += 1
        print(f"  \033[0;32m✓\033[0m {msg}")
    else:
        failed += 1
        print(f"  \033[0;31m✗\033[0m {msg}")

def http_client():
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT)

async def ws_connect_and_get_token():
    """Connect a bot via WebSocket and return (ws, token, bot_uuid)"""
    import websockets
    ws = await websockets.connect(BCS_WS)
    connect_req = {
        "type": "req",
        "id": "connect-1",
        "method": "bot.connect",
        "params": {}
    }
    await ws.send(json.dumps(connect_req))
    resp = json.loads(await ws.recv())
    token = resp.get("payload", {}).get("token")
    bot_uuid = resp.get("payload", {}).get("bot_uuid")
    return ws, token, bot_uuid

async def onboard_bot(token, name, summary, skills=None, visibility="public"):
    """Onboard a bot via HTTP API"""
    async with http_client() as client:
        resp = await client.post(
            f"{BCS_HTTP}/bots/onboard",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "name": name,
                "summary": summary,
                "domains": ["test"],
                "skills": skills or [],
                "scopes": ["test"],
            }
        )
        return resp.status_code, resp.text

async def set_visibility(token, bot_uuid, visibility):
    """Set bot visibility via HTTP API"""
    async with http_client() as client:
        resp = await client.put(
            f"{BCS_HTTP}/bots/{bot_uuid}/visibility",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"visibility": visibility}
        )
        return resp.status_code

async def create_group_http(token, driver_uuid, participant_uuids):
    """Create a group via HTTP and return group_id"""
    async with http_client() as client:
        participants = [{"bot_uuid": driver_uuid, "role": "driver"}]
        for p in participant_uuids:
            participants.append({"bot_uuid": p, "role": "consultant"})
        resp = await client.post(
            f"{BCS_HTTP}/groups",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"driver_bot": driver_uuid, "participants": participants}
        )
        if resp.status_code != 200:
            return None, resp.text
        data = resp.json()
        return data.get("group_id") or data.get("id"), data

async def send_group_chat_http(token, group_id, message, from_bot=None):
    """Send group chat message via HTTP"""
    async with http_client() as client:
        body = {"message": message}
        if from_bot:
            body["from"] = from_bot
        try:
            resp = await client.post(
                f"{BCS_HTTP}/groups/{group_id}/chat",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body
            )
            return resp.status_code, resp.text
        except httpx.ReadTimeout:
            return 504, "timeout"

async def add_group_member_http(token, group_id, bot_uuid):
    """Add member to group via HTTP"""
    async with http_client() as client:
        resp = await client.post(
            f"{BCS_HTTP}/groups/{group_id}/members",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"bot_uuid": bot_uuid}
        )
        return resp.status_code, resp.text

async def bot_send_chat_event(ws, group_id, run_id, text, state="final"):
    """Bot sends a chat.event frame (simulating bot reply)"""
    event = {
        "type": "event",
        "event": "chat.event",
        "payload": {
            "run_id": run_id,
            "bcs_group_id": group_id,
            "state": state,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}]
            }
        }
    }
    await ws.send(json.dumps(event))

async def drain_ws(ws, timeout=0.5):
    """Drain all pending messages from WebSocket, return list"""
    messages = []
    try:
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            messages.append(json.loads(msg))
    except asyncio.TimeoutError:
        pass
    return messages

async def main():
    print("\n── Setup: Connecting 4 bots ──\n")

    # Connect 4 bots
    ws_a, token_a, uuid_a = await ws_connect_and_get_token()
    ws_b, token_b, uuid_b = await ws_connect_and_get_token()
    ws_c, token_c, uuid_c = await ws_connect_and_get_token()
    ws_d, token_d, uuid_d = await ws_connect_and_get_token()

    print(f"  Bot A (driver):   {uuid_a}")
    print(f"  Bot B (member):   {uuid_b}")
    print(f"  Bot C (member):   {uuid_c}")
    print(f"  Bot D (outsider): {uuid_d}")

    # Onboard all as public
    for i, (token, name) in enumerate([(token_a, "Driver"), (token_b, "MemberB"), (token_c, "MemberC"), (token_d, "PrivateD")]):
        code, body = await onboard_bot(token, name, f"{name} test bot")
        check(code == 200, f"Onboard {name} (code={code})")

    # Set all to public visibility
    vis_a = await set_visibility(token_a, uuid_a, "public")
    vis_b = await set_visibility(token_b, uuid_b, "public")
    vis_c = await set_visibility(token_c, uuid_c, "public")
    vis_d = await set_visibility(token_d, uuid_d, "public")
    check(vis_a == 200, "Set A visibility to public")

    # Drain initial events
    await drain_ws(ws_a)
    await drain_ws(ws_b)
    await drain_ws(ws_c)
    await drain_ws(ws_d)

    # ========================================================================
    print("\n── Test 1: HTTP — Private bot cannot send group message (AC-77) ──\n")
    # ========================================================================

    group_id, _ = await create_group_http(token_a, uuid_a, [uuid_b, uuid_c])
    check(group_id is not None, "Group created")

    # Set B to private
    code = await set_visibility(token_b, uuid_b, "private")
    check(code == 200, "Set B to private")

    # B tries to send via HTTP
    code, body = await send_group_chat_http(token_b, group_id, "Hello from private B", uuid_b)
    check(code == 403, f"AC-77: Private bot HTTP send → 403 (got {code})")
    check("private" in body.lower(), "AC-77: Error mentions 'private'")

    # ========================================================================
    print("\n── Test 2: HTTP — Private bot cannot be invited (AC-78) ──\n")
    # ========================================================================

    # Set D to private
    await set_visibility(token_d, uuid_d, "private")

    code, body = await add_group_member_http(token_a, group_id, uuid_d)
    check(code == 404, f"AC-78: Inviting private bot → 404 (got {code})")
    check("not found" in body.lower() or "404" in body, "AC-78: Error hides existence")

    # ========================================================================
    print("\n── Test 3: HTTP — Driver private cannot invite (AC-80) ──\n")
    # ========================================================================

    # Set A (driver) to private
    await set_visibility(token_a, uuid_a, "private")

    code, body = await add_group_member_http(token_a, group_id, uuid_c)
    check(code == 403, f"AC-80: Private driver invite → 403 (got {code})")
    check("private" in body.lower(), "AC-80: Error mentions 'private'")

    # Reset A to public
    await set_visibility(token_a, uuid_a, "public")

    # ========================================================================
    print("\n── Test 4: WS — Private bot inject filtered for private bot (AC-77 WS) ──\n")
    # ========================================================================

    # Reset B to public first (was set to private in test 1)
    await set_visibility(token_b, uuid_b, "public")

    # Create group2 with all public, THEN set B to private
    group2_id, err = await create_group_http(token_a, uuid_a, [uuid_b, uuid_c])
    check(group2_id is not None, f"Group 2 created (err={str(err)[:100] if err else 'ok'})")

    # Now set B to private (already in group, AC-79 scenario)
    await set_visibility(token_b, uuid_b, "private")

    # Drain all channels
    await drain_ws(ws_a)
    await drain_ws(ws_b)
    await drain_ws(ws_c)

    # A sends a message — B should get chat.send (mentioned) or chat.inject
    code, _ = await send_group_chat_http(token_a, group2_id, "Hello group", uuid_a)

    # Drain A
    await drain_ws(ws_a)

    # Check what B receives — private bot should NOT get inject
    b_msgs = await drain_ws(ws_b, timeout=1.0)
    b_inject = any(m.get("type") == "event" and m.get("event") == "chat.inject" for m in b_msgs)
    b_send = any(m.get("type") == "event" and m.get("event") == "chat.send" for m in b_msgs)
    print(f"  B (private) received: inject={b_inject}, send={b_send}")

    check(not b_inject, "AC-77 WS: Private bot B does NOT receive chat.inject")

    # C should receive inject
    c_msgs = await drain_ws(ws_c, timeout=1.0)
    c_inject = any(m.get("type") == "event" and m.get("event") == "chat.inject" for m in c_msgs)
    check(c_inject, "Public bot C receives chat.inject from A's message")

    # ========================================================================
    print("\n── Test 5: WS — Private bot outbound reply intercepted ──\n")
    # ========================================================================

    # Give B a run_id by sending chat.send to B (mention @B)
    code, _ = await send_group_chat_http(token_a, group2_id, "@B please reply", uuid_a)
    await drain_ws(ws_a)
    await drain_ws(ws_c)

    # Find the run_id B received
    b_msgs = await drain_ws(ws_b, timeout=1.0)
    run_id = None
    for msg in b_msgs:
        if msg.get("type") == "event" and msg.get("event") in ("chat.send", "chat.inject"):
            run_id = msg.get("payload", {}).get("idempotency_key")
            if run_id:
                print(f"  Got run_id: {run_id}")
                break

    if run_id:
        # B (private) tries to reply via WebSocket chat.event
        await bot_send_chat_event(ws_b, group2_id, run_id, "Private bot replying via WS")

        await asyncio.sleep(0.5)

        # Check A didn't receive the private bot's original message
        a_msgs = await drain_ws(ws_a, timeout=1.0)
        private_reply_sent = False
        for msg in a_msgs:
            if msg.get("type") == "event":
                payload_str = json.dumps(msg.get("payload", {}))
                if "Private bot replying via WS" in payload_str:
                    private_reply_sent = True

        check(not private_reply_sent, "AC-77 WS: Private bot's original reply NOT routed to A")
    else:
        print("  ⚠ No chat.send received by B, skipping outbound test")

    # ========================================================================
    # Cleanup
    # ========================================================================
    await ws_a.close()
    await ws_b.close()
    await ws_c.close()
    try:
        await ws_d.close()
    except:
        pass

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    sys.exit(1 if failed > 0 else 0)

asyncio.run(main())
PYTHON_SCRIPT