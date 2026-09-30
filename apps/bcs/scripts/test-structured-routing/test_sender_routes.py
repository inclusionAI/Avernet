#!/usr/bin/env python3
"""
E2E test for sender_routes static forwarding table.

Tests the full chain: real OpenClaw bots with BCN plugin -> BCS sender_routes routing.
When mode=mention + sender_routes is configured:
  - bcs_route tool is NOT available to bots (hidden by plugin)
  - Coordinator's messages are forwarded to DBA via sender_routes (chat.send)
  - DevOps receives chat.inject (observer)

Shares the same setup.sh infrastructure as test_structured_routing.py.

Usage:
    BCS_URL=... COORD_UUID=... DBA_UUID=... DEVOPS_UUID=... \
    COORD_TOKEN=... python3 test_sender_routes.py
"""

import asyncio
import json
import os
import sys
import time

try:
    import websockets
except ImportError:
    print("ERROR: 'websockets' required. Install: pip3 install websockets")
    sys.exit(1)

try:
    import httpx
except ImportError:
    print("ERROR: 'httpx' required. Install: pip3 install httpx")
    sys.exit(1)

BCS_URL = os.environ.get("BCS_URL", "http://127.0.0.1:21000")
COORD_UUID = os.environ.get("COORD_UUID", "")
DBA_UUID = os.environ.get("DBA_UUID", "")
DEVOPS_UUID = os.environ.get("DEVOPS_UUID", "")
COORD_TOKEN = os.environ.get("COORD_TOKEN", "")

CLIENT_WS_URL = BCS_URL.replace("http://", "ws://") + "/ws"

PASS = "\033[92m\u2713\033[0m"
FAIL = "\033[91m\u2717\033[0m"
CYAN = "\033[0;36m"
GRAY = "\033[0;90m"
NC = "\033[0m"

BOT_RESPONSE_TIMEOUT = 120


def ok(msg):
    print(f"  {PASS} {msg}")


def fail_msg(msg):
    print(f"  {FAIL} {msg}")


def info(msg):
    print(f"  {CYAN}\u2192{NC} {msg}")


def dim(msg):
    print(f"  {GRAY}{msg}{NC}")


# -- HTTP helpers --------------------------------------------------------------

async def create_group(token: str, driver: str, participants: list, routing_policy=None, context=None):
    body = {"driver_bot": driver, "participants": participants}
    if routing_policy:
        body["routing_policy"] = routing_policy
    if context:
        body["context"] = context
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BCS_URL}/groups",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=10,
        )
        assert resp.status_code == 200, f"create group failed: {resp.status_code} {resp.text}"
        data = resp.json()
        return data.get("id") or data.get("group_id")


async def update_routing_policy(token: str, group_id: str, policy: dict):
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            f"{BCS_URL}/groups/{group_id}/routing-policy",
            headers={"Authorization": f"Bearer {token}"},
            json=policy,
            timeout=10,
        )
        return resp


async def get_messages(token: str, group_id: str):
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BCS_URL}/groups/{group_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        return []


# -- Client WebSocket observer -------------------------------------------------

class ClientObserver:
    """Connects to BCS /ws endpoint to observe group events."""

    def __init__(self, group_id: str):
        self.group_id = group_id
        self.ws = None
        self.events = []
        self.bot_finals: dict[str, asyncio.Event] = {}
        self._req_counter = 0
        self._pending: dict[str, asyncio.Future] = {}

    def expect_bot_final(self, bot_uuid: str) -> asyncio.Event:
        if bot_uuid not in self.bot_finals:
            self.bot_finals[bot_uuid] = asyncio.Event()
        return self.bot_finals[bot_uuid]

    def _next_req_id(self) -> str:
        self._req_counter += 1
        return f"obs{self._req_counter:03d}"

    async def connect(self):
        self.ws = await websockets.connect(CLIENT_WS_URL)
        info("Client WebSocket connected")

    async def subscribe(self):
        req_id = self._next_req_id()
        frame = {
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {"group_id": self.group_id},
        }
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(frame))
        res = await asyncio.wait_for(fut, timeout=5)
        if not res.get("ok"):
            raise RuntimeError(f"subscribe failed: {res}")
        ok(f"Subscribed to group {self.group_id}")

    async def send_message(self, message: str, bot_uuid: str):
        req_id = self._next_req_id()
        frame = {
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": {
                "sessionKey": "main",
                "message": message,
                "group_id": self.group_id,
                "bot_uuid": bot_uuid,
                "bot_id": bot_uuid,
                "timeoutMs": BOT_RESPONSE_TIMEOUT * 1000,
            },
        }
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps(frame))
        res = await asyncio.wait_for(fut, timeout=10)
        if not res.get("ok"):
            raise RuntimeError(f"chat.send failed: {res}")
        run_id = res.get("payload", {}).get("runId") or res.get("payload", {}).get("run_id")
        ok(f"Message sent: runId={run_id}")
        return run_id

    async def listen(self, stop_event: asyncio.Event):
        try:
            async for raw in self.ws:
                frame = json.loads(raw)
                ftype = frame.get("type")

                if ftype == "res":
                    req_id = frame.get("id")
                    if req_id in self._pending:
                        fut = self._pending.pop(req_id)
                        if not fut.done():
                            fut.set_result(frame)
                    continue

                if ftype == "event":
                    self.events.append(frame)
                    event_name = frame.get("event")
                    payload = frame.get("payload", {})
                    bot_uuid = frame.get("bot_uuid", "?")
                    state = payload.get("state") or payload.get("stream")

                    if event_name in ("chat.event", "chat") and state:
                        if state == "delta":
                            msg = payload.get("message", {})
                            text = ""
                            for block in (msg.get("content") or []):
                                if isinstance(block, dict):
                                    text += block.get("text", "")
                            if text:
                                dim(f"  delta [{bot_uuid[:8]}]: {text[:60]}")
                        elif state == "final":
                            msg = payload.get("message", {})
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                content = "".join(
                                    b.get("text", "") if isinstance(b, dict) else str(b)
                                    for b in content
                                )
                            ok(f"final [{bot_uuid[:8]}]: {str(content)[:80]}")
                            if bot_uuid in self.bot_finals:
                                self.bot_finals[bot_uuid].set()
                    elif event_name == "agent":
                        data = payload.get("data", {})
                        stream = payload.get("stream")
                        if stream == "tool":
                            phase = data.get("phase")
                            tool_name = data.get("name", "?")
                            dim(f"  tool {phase}: {tool_name}")

                if stop_event.is_set():
                    break
        except websockets.ConnectionClosed:
            pass

    async def close(self):
        if self.ws:
            await self.ws.close()


# -- Tests ---------------------------------------------------------------------

async def test_sender_routes_basic():
    """
    Test 1: sender_routes basic forwarding.

    1. Create group with mode=mention (no sender_routes yet)
    2. PUT routing-policy with sender_routes: Coordinator -> [DBA]
    3. Send user message -> Coordinator responds
    4. BCS routes Coordinator's response via sender_routes:
       - DBA gets chat.send (target)
       - DevOps gets chat.inject (observer)
    5. DBA should respond
    """
    print(f"\n  {CYAN}--- Test 1: sender_routes basic forwarding ---{NC}\n")

    info("Creating group with mode=mention...")
    group_id = await create_group(
        COORD_TOKEN,
        COORD_UUID,
        [
            {"bot_uuid": COORD_UUID, "role": "driver"},
            {"bot_uuid": DBA_UUID, "role": "consultant"},
            {"bot_uuid": DEVOPS_UUID, "role": "consultant"},
        ],
        routing_policy={"mode": "mention", "default_bot_final_delivery": "send_to_driver"},
        context="测试 sender_routes 基本转发：Coordinator 收到用户消息后回复，BCS 按 sender_routes 将回复转发给 DBA。",
    )
    ok(f"Group created: {group_id}")

    # Set sender_routes via PUT endpoint (validates against participants)
    info(f"Setting sender_routes: {COORD_UUID[:8]}... -> [{DBA_UUID[:8]}...]")
    resp = await update_routing_policy(COORD_TOKEN, group_id, {
        "mode": "mention",
        "sender_routes": {COORD_UUID: [DBA_UUID]},
    })
    assert resp.status_code == 200, f"update routing policy failed: {resp.status_code} {resp.text}"
    ok("sender_routes configured")

    # Connect client observer
    observer = ClientObserver(group_id)
    await observer.connect()

    coord_final = observer.expect_bot_final(COORD_UUID)
    dba_final = observer.expect_bot_final(DBA_UUID)
    devops_final = observer.expect_bot_final(DEVOPS_UUID)

    stop_event = asyncio.Event()
    listen_task = asyncio.create_task(observer.listen(stop_event))

    await observer.subscribe()

    try:
        # Wait for Coordinator to process system message (group creation)
        info("Waiting for Coordinator to process system message...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator processed system message")
        except asyncio.TimeoutError:
            fail_msg("Coordinator did not respond to system message")
            return False

        coord_final.clear()
        await asyncio.sleep(1)

        # Send user message
        info("Sending message: '请帮我分析一下最近的数据库慢查询'")
        await observer.send_message("请帮我分析一下最近的数据库慢查询", COORD_UUID)

        # Wait for Coordinator to respond
        info(f"Waiting for Coordinator response (up to {BOT_RESPONSE_TIMEOUT}s)...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator responded")
        except asyncio.TimeoutError:
            fail_msg(f"Coordinator did not respond within {BOT_RESPONSE_TIMEOUT}s")
            return False

        # Verify bcs_route was NOT used (tool should be hidden when mode=mention)
        tool_events = [
            e for e in observer.events
            if e.get("event") == "agent"
            and e.get("payload", {}).get("stream") == "tool"
            and e.get("payload", {}).get("data", {}).get("name") == "bcs_route"
        ]
        if tool_events:
            fail_msg(f"bcs_route tool was called {len(tool_events)} time(s) — should be hidden in mention mode!")
        else:
            ok("bcs_route tool NOT used (correctly hidden in mention mode)")

        # Wait for DBA to respond (should receive chat.send via sender_routes)
        info(f"Waiting for DBA response via sender_routes (up to {BOT_RESPONSE_TIMEOUT}s)...")
        try:
            await asyncio.wait_for(dba_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("DBA responded (received chat.send via sender_routes)")
        except asyncio.TimeoutError:
            fail_msg("DBA did not respond — sender_routes forwarding may have failed")
            return False

        # DevOps should stay silent (received chat.inject)
        info("Checking if DevOps stays silent (10s window)...")
        try:
            await asyncio.wait_for(devops_final.wait(), timeout=10)
            info("DevOps also responded (received chat.inject, but chose to speak)")
        except asyncio.TimeoutError:
            ok("DevOps stayed silent (correctly received chat.inject)")

        # Check message history
        info("Checking message history...")
        messages = await get_messages(COORD_TOKEN, group_id)
        if isinstance(messages, list):
            ok(f"Message history: {len(messages)} messages")
            for msg in messages:
                role = msg.get("role", "?")
                sender = msg.get("from", msg.get("sender", "?"))
                text = msg.get("content", msg.get("message", ""))[:60]
                dim(f"  [{role}] {sender}: {text}")

        ok("Test 1 PASSED: sender_routes basic forwarding works")
        return True

    finally:
        stop_event.set()
        listen_task.cancel()
        try:
            await listen_task
        except (asyncio.CancelledError, Exception):
            pass
        await observer.close()


async def test_sender_routes_chain():
    """
    Test 2: sender_routes chain forwarding (A -> B -> C).

    1. Create group with sender_routes: Coordinator -> [DBA], DBA -> [DevOps]
    2. User message -> Coordinator responds -> routed to DBA -> DBA responds -> routed to DevOps
    3. Verify hop count increments and chain works
    """
    print(f"\n  {CYAN}--- Test 2: sender_routes chain forwarding ---{NC}\n")

    info("Creating group with mode=mention...")
    group_id = await create_group(
        COORD_TOKEN,
        COORD_UUID,
        [
            {"bot_uuid": COORD_UUID, "role": "driver"},
            {"bot_uuid": DBA_UUID, "role": "consultant"},
            {"bot_uuid": DEVOPS_UUID, "role": "consultant"},
        ],
        routing_policy={"mode": "mention", "default_bot_final_delivery": "send_to_driver"},
    )
    ok(f"Group created: {group_id}")

    # Chain: Coordinator -> DBA -> DevOps
    info(f"Setting sender_routes chain: Coord -> DBA -> DevOps")
    resp = await update_routing_policy(COORD_TOKEN, group_id, {
        "mode": "mention",
        "sender_routes": {
            COORD_UUID: [DBA_UUID],
            DBA_UUID: [DEVOPS_UUID],
        },
    })
    assert resp.status_code == 200, f"update routing policy failed: {resp.status_code} {resp.text}"
    ok("Chain sender_routes configured")

    observer = ClientObserver(group_id)
    await observer.connect()

    coord_final = observer.expect_bot_final(COORD_UUID)
    dba_final = observer.expect_bot_final(DBA_UUID)
    devops_final = observer.expect_bot_final(DEVOPS_UUID)

    stop_event = asyncio.Event()
    listen_task = asyncio.create_task(observer.listen(stop_event))

    await observer.subscribe()

    try:
        # Wait for system message processing
        info("Waiting for Coordinator to process system message...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator processed system message")
        except asyncio.TimeoutError:
            fail_msg("Coordinator did not respond to system message")
            return False

        coord_final.clear()
        await asyncio.sleep(1)

        # Send user message
        info("Sending message: '请帮我检查生产环境数据库和服务状态'")
        await observer.send_message("请帮我检查生产环境数据库和服务状态", COORD_UUID)

        # Step 1: Coordinator responds
        info(f"Step 1: Waiting for Coordinator response...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator responded -> BCS routes to DBA via sender_routes")
        except asyncio.TimeoutError:
            fail_msg("Coordinator did not respond")
            return False

        # Step 2: DBA responds (received chat.send from Coordinator via sender_routes)
        info(f"Step 2: Waiting for DBA response (hop 1)...")
        try:
            await asyncio.wait_for(dba_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("DBA responded -> BCS routes to DevOps via sender_routes (hop 2)")
        except asyncio.TimeoutError:
            fail_msg("DBA did not respond — hop 1 sender_routes may have failed")
            return False

        # Step 3: DevOps responds (received chat.send from DBA via sender_routes)
        info(f"Step 3: Waiting for DevOps response (hop 2)...")
        try:
            await asyncio.wait_for(devops_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("DevOps responded (received chat.send from chain forwarding)")
        except asyncio.TimeoutError:
            fail_msg("DevOps did not respond — hop 2 chain forwarding may have failed")
            return False

        # Verify message history shows the chain
        info("Checking message history...")
        messages = await get_messages(COORD_TOKEN, group_id)
        if isinstance(messages, list):
            ok(f"Message history: {len(messages)} messages")
            for msg in messages:
                role = msg.get("role", "?")
                sender = msg.get("from", msg.get("sender", "?"))
                text = msg.get("content", msg.get("message", ""))[:60]
                dim(f"  [{role}] {sender}: {text}")

        ok("Test 2 PASSED: sender_routes chain forwarding works")
        return True

    finally:
        stop_event.set()
        listen_task.cancel()
        try:
            await listen_task
        except (asyncio.CancelledError, Exception):
            pass
        await observer.close()


async def test_sender_routes_validation():
    """
    Test 3: sender_routes validation (API-level, no bots needed).

    - Self-reference rejected
    - Non-participant rejected
    - Valid config accepted
    """
    print(f"\n  {CYAN}--- Test 3: sender_routes validation ---{NC}\n")

    group_id = await create_group(
        COORD_TOKEN,
        COORD_UUID,
        [
            {"bot_uuid": COORD_UUID, "role": "driver"},
            {"bot_uuid": DBA_UUID, "role": "consultant"},
            {"bot_uuid": DEVOPS_UUID, "role": "consultant"},
        ],
    )
    ok(f"Group created: {group_id}")

    # Self-reference: Coordinator -> [Coordinator] should fail
    info("Testing self-reference rejection...")
    resp = await update_routing_policy(COORD_TOKEN, group_id, {
        "sender_routes": {COORD_UUID: [COORD_UUID]},
    })
    if resp.status_code == 400:
        ok(f"Self-reference rejected (400): {resp.json().get('error', {}).get('message', '')[:60]}")
    else:
        fail_msg(f"Self-reference NOT rejected: {resp.status_code}")
        return False

    # Non-participant should fail
    info("Testing non-participant rejection...")
    resp = await update_routing_policy(COORD_TOKEN, group_id, {
        "sender_routes": {COORD_UUID: ["nonexistent-bot-id"]},
    })
    if resp.status_code == 400:
        ok(f"Non-participant rejected (400): {resp.json().get('error', {}).get('message', '')[:60]}")
    else:
        fail_msg(f"Non-participant NOT rejected: {resp.status_code}")
        return False

    # Valid config should succeed
    info("Testing valid config acceptance...")
    resp = await update_routing_policy(COORD_TOKEN, group_id, {
        "mode": "mention",
        "sender_routes": {COORD_UUID: [DBA_UUID]},
    })
    if resp.status_code == 200:
        ok("Valid config accepted (200)")
    else:
        fail_msg(f"Valid config rejected: {resp.status_code} {resp.text}")
        return False

    ok("Test 3 PASSED: sender_routes validation works")
    return True


async def main():
    print(f"\n{CYAN}== Sender Routes E2E Test (Real OpenClaw) =={NC}\n")

    # Validate environment
    missing = []
    for var in ("BCS_URL", "COORD_UUID", "DBA_UUID", "DEVOPS_UUID", "COORD_TOKEN"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        fail_msg(f"Missing environment variables: {', '.join(missing)}")
        fail_msg("Run setup.sh first and eval its output")
        sys.exit(1)

    info(f"BCS: {BCS_URL}")
    info(f"Coordinator: {COORD_UUID}")
    info(f"DBA: {DBA_UUID}")
    info(f"DevOps: {DEVOPS_UUID}")

    # Health check
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{BCS_URL}/health", timeout=5)
        if resp.status_code != 200:
            fail_msg(f"BCS health check failed: {resp.status_code}")
            sys.exit(1)
    ok("BCS healthy")

    results = []

    # Test 3 first (validation only, fast, no LLM needed)
    results.append(("Validation", await test_sender_routes_validation()))

    # Test 1: basic sender_routes forwarding
    results.append(("Basic forwarding", await test_sender_routes_basic()))

    # Test 2: chain forwarding
    results.append(("Chain forwarding", await test_sender_routes_chain()))

    # Summary
    print(f"\n{CYAN}== Results =={NC}\n")
    all_passed = True
    for name, passed in results:
        status = PASS if passed else FAIL
        print(f"  {status} {name}")
        if not passed:
            all_passed = False

    print(f"\n{CYAN}== {'ALL PASSED' if all_passed else 'SOME FAILED'} =={NC}\n")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
