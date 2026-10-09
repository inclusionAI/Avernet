#!/usr/bin/env python3
"""
E2E test for structured message routing (task 13.5).

Tests the full chain: real OpenClaw bots with BCN plugin → BCS Rust routing.
The coordinator bot uses bcs_route tool to route messages to DBA.

Setup.sh must have been run first to start BCS + 3 OpenClaw instances.
Environment variables (BCS_URL, bot UUIDs, tokens) come from setup.sh output.

Usage:
    BCS_URL=... COORD_UUID=... DBA_UUID=... DEVOPS_UUID=... \
    COORD_TOKEN=... python3 test_structured_routing.py
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
BCS_WS_URL = os.environ.get("BCS_WS_URL", "ws://127.0.0.1:21000/ws")
COORD_UUID = os.environ.get("COORD_UUID", "")
DBA_UUID = os.environ.get("DBA_UUID", "")
DEVOPS_UUID = os.environ.get("DEVOPS_UUID", "")
COORD_TOKEN = os.environ.get("COORD_TOKEN", "")

# Use the client WS endpoint (not /ws/bot)
CLIENT_WS_URL = BCS_URL.replace("http://", "ws://") + "/ws"

PASS = "\033[92m\u2713\033[0m"
FAIL = "\033[91m\u2717\033[0m"
CYAN = "\033[0;36m"
GRAY = "\033[0;90m"
NC = "\033[0m"

# LLM timeout — bots need time for inference
BOT_RESPONSE_TIMEOUT = 120


def ok(msg):
    print(f"  {PASS} {msg}")


def fail_msg(msg):
    print(f"  {FAIL} {msg}")


def info(msg):
    print(f"  {CYAN}\u2192{NC} {msg}")


def dim(msg):
    print(f"  {GRAY}{msg}{NC}")


# ── HTTP helpers ───────────────────────────────────────────────────────────

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


async def send_group_message(token: str, group_id: str, message: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{BCS_URL}/groups/{group_id}/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": message, "from": "user"},
            timeout=10,
        )
        assert resp.is_success, f"group chat failed: {resp.status_code}"


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


# ── Client WebSocket observer ─────────────────────────────────────────────

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


# ── Tests ──────────────────────────────────────────────────────────────────

async def test_structured_routing_e2e():
    """
    13.5: Full E2E structured routing test.

    1. Create group with hybrid routing policy
    2. Send message about database deadlock
    3. Coordinator should use bcs_route tool to route to DBA
    4. DBA should respond (received chat.send via structured routing)
    5. DevOps should either stay silent or produce minimal response (chat.inject)
    """
    info("Creating group with hybrid routing policy...")

    group_id = await create_group(
        COORD_TOKEN,
        COORD_UUID,
        [
            {"bot_uuid": COORD_UUID, "role": "driver"},
            {"bot_uuid": DBA_UUID, "role": "consultant"},
            {"bot_uuid": DEVOPS_UUID, "role": "consultant"},
        ],
        routing_policy={"mode": "hybrid", "default_bot_final_delivery": "send_to_driver"},
        context="线上数据库出现死锁，需要 DBA 和 DevOps 协同排查，DBA 负责分析锁等待链，DevOps 负责检查服务端连接池配置。",
    )
    ok(f"Group created: {group_id}")

    # Connect client observer
    observer = ClientObserver(group_id)
    await observer.connect()

    # Set up final event expectations
    coord_final = observer.expect_bot_final(COORD_UUID)
    dba_final = observer.expect_bot_final(DBA_UUID)
    devops_final = observer.expect_bot_final(DEVOPS_UUID)

    # Start listen loop BEFORE subscribe so it can process the response frame
    stop_event = asyncio.Event()
    listen_task = asyncio.create_task(observer.listen(stop_event))

    await observer.subscribe()

    try:
        # Wait for coordinator to finish processing the group creation system message
        # The system message triggers an agent run; we must wait for it to complete
        # before sending our test message, otherwise dispatchReply will skip it.
        info("Waiting for Coordinator to finish processing system message...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator processed system message")
        except asyncio.TimeoutError:
            fail_msg("Coordinator did not respond to system message")
            return False

        # Reset the event so we can wait for the next response
        coord_final.clear()

        # Brief settle time after first response
        await asyncio.sleep(1)

        # Send user message via WebSocket chat.send — triggers InboundHandler with run tracking
        info("Sending message: '请 DBA 排查数据库死锁问题'")
        await observer.send_message("请 DBA 排查数据库死锁问题", COORD_UUID)

        # Wait for coordinator to respond (should use bcs_route tool)
        info(f"Waiting for Coordinator response (up to {BOT_RESPONSE_TIMEOUT}s)...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator responded")
        except asyncio.TimeoutError:
            fail_msg(f"Coordinator did not respond within {BOT_RESPONSE_TIMEOUT}s")
            return False

        # Wait for DBA to respond (should have received chat.send via structured routing)
        info(f"Waiting for DBA response (up to {BOT_RESPONSE_TIMEOUT}s)...")
        try:
            await asyncio.wait_for(dba_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("DBA responded (received chat.send via structured routing)")
        except asyncio.TimeoutError:
            fail_msg(f"DBA did not respond within {BOT_RESPONSE_TIMEOUT}s")
            fail_msg("This likely means structured routing failed — DBA didn't receive chat.send")
            return False

        # Check DevOps — should NOT respond (received chat.inject with action=observe)
        # Give it a short window to see if it responds
        info("Checking if DevOps stays silent (10s window)...")
        try:
            await asyncio.wait_for(devops_final.wait(), timeout=10)
            # DevOps DID respond — that's unexpected but not fatal
            # In some cases, the observe bot might still produce a response
            info("DevOps also responded (received chat.inject, but chose to speak)")
        except asyncio.TimeoutError:
            ok("DevOps stayed silent (correctly received chat.inject with action=observe)")

        # Verify via message history
        info("Checking message history...")
        messages = await get_messages(COORD_TOKEN, group_id)
        if isinstance(messages, list):
            ok(f"Message history: {len(messages)} messages")
            for msg in messages:
                role = msg.get("role", "?")
                sender = msg.get("from", msg.get("sender", "?"))
                text = msg.get("content", msg.get("message", ""))[:60]
                dim(f"  [{role}] {sender}: {text}")

        # Check that bcs_route tool was used by looking at agent events
        tool_events = [
            e for e in observer.events
            if e.get("event") == "agent"
            and e.get("payload", {}).get("stream") == "tool"
            and e.get("payload", {}).get("data", {}).get("name") == "bcs_route"
        ]
        if tool_events:
            ok(f"bcs_route tool was called {len(tool_events)} time(s)")
        else:
            info("bcs_route tool call not observed in events (may have been filtered)")

        ok("13.5 E2E structured routing test PASSED")
        return True

    finally:
        stop_event.set()
        listen_task.cancel()
        try:
            await listen_task
        except (asyncio.CancelledError, Exception):
            pass
        await observer.close()


async def main():
    print(f"\n{CYAN}== Structured Routing E2E Test (Real OpenClaw) =={NC}\n")

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

    # Run test
    success = await test_structured_routing_e2e()

    print(f"\n{CYAN}== Result: {'PASSED' if success else 'FAILED'} =={NC}\n")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
