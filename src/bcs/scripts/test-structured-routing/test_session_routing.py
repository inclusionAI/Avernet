#!/usr/bin/env python3
"""
E2E test for Task 14: session-aware chat.send routing.

Walks the full chain:
  1. POST /groups            → create a group
  2. POST /groups/{id}/sessions → create a session
  3. WS /ws (workbench)      → connect + send chat.send with `session_id` set
  4. Verify each connected bot actually receives the chat.send/inject

Setup.sh must have been run first to start BCS + 3 OpenClaw bots.
Environment variables (BCS_URL, COORD_UUID, …) come from setup.sh output.

Usage:
    BCS_URL=... BCS_WS_URL=... COORD_UUID=... DBA_UUID=... DEVOPS_UUID=... \\
    COORD_TOKEN=... python3 test_session_routing.py
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

# `BCS_AUTH_MOCK=1` mode in the BCS server: WS upgrade reads these headers
# (or the BCS_MOCK_USER_* env vars) to synthesize a Human cookie identity.
MOCK_USER_ID = os.environ.get("BCS_MOCK_USER_ID", "11111111")
MOCK_HEADERS = {
    "X-Mock-User-Id": MOCK_USER_ID,
    "X-Mock-Nick-Name": os.environ.get("BCS_MOCK_USER_NICK_NAME", "LocalDev"),
}

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
CYAN = "\033[0;36m"
GRAY = "\033[0;90m"
NC = "\033[0m"

BOT_RESPONSE_TIMEOUT = 60  # seconds — bots may need LLM inference time


def ok(msg):
    print(f"  {PASS} {msg}")


def fail_msg(msg):
    print(f"  {FAIL} {msg}")


def info(msg):
    print(f"  {CYAN}→{NC} {msg}")


def dim(msg):
    print(f"  {GRAY}{msg}{NC}")


# ── HTTP helpers ───────────────────────────────────────────────────────────


async def http_post(client, path, *, token, json_body=None, headers=None):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    if headers:
        h.update(headers)
    return await client.post(f"{BCS_URL}{path}", json=json_body or {}, headers=h, timeout=10)


async def create_group(client, driver, participants):
    resp = await http_post(
        client,
        "/groups",
        token=COORD_TOKEN,
        json_body={"driver_bot": driver, "participants": participants},
    )
    assert resp.status_code == 200, f"create group failed: {resp.status_code} {resp.text}"
    data = resp.json()
    return data.get("id") or data.get("group_id")


async def create_session(client, group_id):
    resp = await http_post(
        client,
        f"/groups/{group_id}/sessions",
        token=COORD_TOKEN,
        json_body={"session_kind": "chat"},
    )
    assert resp.status_code == 201, f"create session failed: {resp.status_code} {resp.text}"
    data = resp.json()
    sid = data.get("session_id")
    assert sid, f"session response missing session_id: {data}"
    return sid


async def get_session(client, session_id):
    resp = await client.get(f"{BCS_URL}/sessions/{session_id}", timeout=10)
    assert resp.status_code == 200, f"get session failed: {resp.status_code} {resp.text}"
    return resp.json()


# ── Workbench WS client ───────────────────────────────────────────────────


class WorkbenchClient:
    """Mimics the frontend workbench: connect + chat.send."""

    def __init__(self, group_id):
        self.group_id = group_id
        self.ws = None
        self.events = []
        self._req_counter = 0
        self._pending = {}
        self._reader_task = None
        self.bot_finals = {}

    def _next_req_id(self):
        self._req_counter += 1
        return f"wb{self._req_counter:03d}"

    def expect_bot_final(self, bot_uuid):
        ev = self.bot_finals.setdefault(bot_uuid, asyncio.Event())
        return ev

    async def connect(self):
        self.ws = await websockets.connect(
            CLIENT_WS_URL,
            additional_headers=MOCK_HEADERS,
        )
        info(f"Workbench WS connected to {CLIENT_WS_URL}")
        self._reader_task = asyncio.create_task(self._reader())

    async def _reader(self):
        try:
            async for raw in self.ws:
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                t = frame.get("type")
                if t == "res":
                    fid = frame.get("id")
                    fut = self._pending.pop(fid, None)
                    if fut and not fut.done():
                        fut.set_result(frame)
                elif t == "event":
                    self._on_event(frame)
        except websockets.ConnectionClosed:
            pass

    def _on_event(self, frame):
        # Workbench server-side wraps bot events as `{type:"event", event:"chat", group_id, bot_uuid, payload}`
        event_name = frame.get("event")
        bot_uuid = frame.get("bot_uuid")
        payload = frame.get("payload") or {}
        state = payload.get("state")
        self.events.append({
            "event": event_name,
            "bot_uuid": bot_uuid,
            "state": state,
            "raw": frame,
        })
        dim(
            f"  ← event={event_name} bot={bot_uuid} state={state}"
        )
        if event_name == "chat" and state == "final" and bot_uuid:
            ev = self.bot_finals.get(bot_uuid)
            if ev:
                ev.set()

    async def subscribe(self):
        req_id = self._next_req_id()
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps({
            "type": "req",
            "id": req_id,
            "method": "connect",
            "params": {"group_id": self.group_id},
        }))
        res = await asyncio.wait_for(fut, timeout=10)
        assert res.get("ok"), f"subscribe failed: {res}"
        ok(f"Subscribed workbench WS to group {self.group_id}")

    async def send(self, message, bot_uuid, *, session_id=None, mentions=None):
        req_id = self._next_req_id()
        params = {
            "sessionKey": "main",
            "message": message,
            "group_id": self.group_id,
            "bot_uuid": bot_uuid,
            "bot_id": bot_uuid,
            "timeoutMs": BOT_RESPONSE_TIMEOUT * 1000,
        }
        if session_id is not None:
            params["session_id"] = session_id
        if mentions:
            params["mentions"] = mentions
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps({
            "type": "req",
            "id": req_id,
            "method": "chat.send",
            "params": params,
        }))
        res = await asyncio.wait_for(fut, timeout=10)
        return res

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            await self.ws.close()


# ── Test phases ───────────────────────────────────────────────────────────


async def phase_create_group_and_session(client):
    info("Phase 1: create group + session")
    participants = [
        {"bot_uuid": COORD_UUID, "role": "driver"},
        {"bot_uuid": DBA_UUID, "role": "consultant"},
        {"bot_uuid": DEVOPS_UUID, "role": "consultant"},
    ]
    group_id = await create_group(client, COORD_UUID, participants)
    ok(f"Group created: {group_id}")

    session_id = await create_session(client, group_id)
    ok(f"Session created: {session_id}")

    sess = await get_session(client, session_id)
    assert sess["status"] == "running", f"session status not running: {sess}"
    assert sess["group_id"] == group_id, f"session.group_id mismatch: {sess}"
    parts = [p.get("bot_uuid") for p in sess.get("participants", [])]
    assert COORD_UUID in parts and DBA_UUID in parts, f"session participants missing bots: {parts}"
    ok(f"Session inherited {len(parts)} participants from group")
    return group_id, session_id


async def phase_send_with_session(group_id, session_id):
    info("Phase 2: send chat.send WITH session_id (Task 14 path)")
    wb = WorkbenchClient(group_id)
    try:
        await wb.connect()
        await wb.subscribe()

        # Pre-arm the final-event waiter on the driver bot (it's the bot that
        # gets chat.send under the no-mention default policy).
        coord_final = wb.expect_bot_final(COORD_UUID)

        res = await wb.send(
            "Hello with session_id",
            bot_uuid=COORD_UUID,
            session_id=session_id,
        )
        assert res.get("ok"), f"chat.send rejected: {res}"
        ok(f"chat.send accepted (runId={res.get('payload', {}).get('runId')})")

        info("Waiting for driver bot to produce final event (LLM round-trip)...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok(f"Driver bot {COORD_UUID} produced final event")
        except asyncio.TimeoutError:
            fail_msg(
                f"Timed out after {BOT_RESPONSE_TIMEOUT}s waiting for driver final event"
            )
            return False

        return True
    finally:
        await wb.close()


async def phase_send_with_invalid_session(group_id):
    info("Phase 3: send chat.send with WRONG session_id (must be rejected)")
    wb = WorkbenchClient(group_id)
    try:
        await wb.connect()
        await wb.subscribe()
        # Use a syntactically valid but non-existent session id under this group.
        bogus = f"{group_id}:deadbeef"
        res = await wb.send(
            "this should be rejected",
            bot_uuid=COORD_UUID,
            session_id=bogus,
        )
        if res.get("ok"):
            fail_msg(
                f"chat.send with bogus session was accepted: {res}"
            )
            return False
        ok(f"chat.send rejected as expected: code={res.get('error', {}).get('code')}")
        return True
    finally:
        await wb.close()


async def phase_send_without_session(group_id):
    info("Phase 4: send chat.send WITHOUT session_id (legacy path, must still work)")
    wb = WorkbenchClient(group_id)
    try:
        await wb.connect()
        await wb.subscribe()
        coord_final = wb.expect_bot_final(COORD_UUID)
        res = await wb.send(
            "legacy hello, no session_id",
            bot_uuid=COORD_UUID,
        )
        assert res.get("ok"), f"legacy chat.send rejected: {res}"
        ok("legacy chat.send (no session_id) accepted")

        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok(f"Driver bot {COORD_UUID} produced final event (legacy path)")
        except asyncio.TimeoutError:
            fail_msg(
                f"Timed out waiting for driver final event in legacy path"
            )
            return False
        return True
    finally:
        await wb.close()


async def main():
    if not (COORD_UUID and DBA_UUID and DEVOPS_UUID and COORD_TOKEN):
        print(
            "Missing one of COORD_UUID / DBA_UUID / DEVOPS_UUID / COORD_TOKEN — "
            "run via run.sh or source the setup.sh output."
        )
        sys.exit(2)

    print()
    print("=" * 64)
    print("Task 14 — Session-aware chat.send routing E2E")
    print("=" * 64)

    failures = []
    async with httpx.AsyncClient() as client:
        try:
            group_id, session_id = await phase_create_group_and_session(client)
        except AssertionError as e:
            fail_msg(f"Phase 1 failed: {e}")
            sys.exit(1)

        if not await phase_send_with_session(group_id, session_id):
            failures.append("Phase 2 (with session_id)")

        if not await phase_send_with_invalid_session(group_id):
            failures.append("Phase 3 (invalid session_id rejection)")

        if not await phase_send_without_session(group_id):
            failures.append("Phase 4 (legacy no session_id)")

    print()
    if failures:
        fail_msg(f"FAILED: {', '.join(failures)}")
        sys.exit(1)
    ok("All phases passed")


if __name__ == "__main__":
    asyncio.run(main())
