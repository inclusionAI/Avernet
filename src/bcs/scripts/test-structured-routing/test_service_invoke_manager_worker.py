#!/usr/bin/env python3
"""
End-to-end test for the Part B service-invocation flow with a
manager-worker group + AntDing callback (Part B Task 5/Task 7).

Walks the full chain:

  1. POST /groups            → create a manager_worker group with
                              service_spec.callback_config (AntDing)
  2. POST /services/{group_id}/sessions
                            → external service-invocation entry,
                              authenticated by the api_key seeded at
                              setup time (X-BCS-Service-Key header).
  3. WS /ws (workbench)     → subscribe and observe events.
  4. Manager bot dispatches via bcs_assign_task; Worker executes.
  5. Manager bot calls bcs_task_complete → BCS completes the
     service-invocation Session and dispatches the post-completion
     callback against service_spec.callback_config.channels.
  6. The test polls Session.callback_status until it reaches a
     terminal state (succeeded / partial_failed / failed) and asserts
     the dispatcher actually fired.

Important:
- This test uses the public AntDing prod endpoint with bogus
  credentials, so the channel always fails (returns business
  success=false). What matters is that BCS records a terminal
  callback_status — proving the end-to-end pipeline ran.
- The api_key + bound_groups must be configured **before** BCS
  starts (Part B Task 3 validates them at startup).

Setup.sh must have been run first.

Usage:
    bash scripts/test-structured-routing/run.sh service-invoke-manager-worker
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

# Service api_key seeded by setup.sh. The raw key must hash to the
# same sha256 that's in the BCS api_keys config.
SERVICE_API_KEY = os.environ.get("SERVICE_API_KEY", "")

CLIENT_WS_URL = BCS_URL.replace("http://", "ws://") + "/ws"

MOCK_USER_ID = os.environ.get("BCS_MOCK_USER_ID", "11111111")
MOCK_HEADERS = {
    "X-Mock-User-Id": MOCK_USER_ID,
    "X-Mock-Nick-Name": "LocalDev",
}

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
CYAN = "\033[0;36m"
GRAY = "\033[0;90m"
NC = "\033[0m"

BOT_RESPONSE_TIMEOUT = 180  # LLM inference + manager_worker round-trip
CALLBACK_POLL_TIMEOUT = 30
CALLBACK_POLL_INTERVAL = 1.0

DEFAULT_ANTDING = {
    "access_key_id": os.environ.get("BCS_TEST_ANTDING_AK", "replace-with-antding-ak"),
    "access_key_secret": os.environ.get("BCS_TEST_ANTDING_SK", "replace-with-antding-sk"),
    "robot_code": os.environ.get("BCS_TEST_ANTDING_ROBOT", "replace-with-antding-robot"),
}


def ok(msg):
    print(f"  {PASS} {msg}")


def fail_msg(msg):
    print(f"  {FAIL} {msg}")


def info(msg):
    print(f"  {CYAN}→{NC} {msg}")


def dim(msg):
    print(f"  {GRAY}{msg}{NC}")


# ── HTTP helpers ────────────────────────────────────────────────────────────


async def http_post(client, path, *, token=None, headers=None, json_body=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    return await client.post(f"{BCS_URL}{path}", json=json_body or {}, headers=h, timeout=15)


async def http_get(client, path, *, token=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {token}"
    return await client.get(f"{BCS_URL}{path}", headers=h, timeout=15)


# ── WS observer (reused from test_manager_worker_session.py) ──────────────


class WorkbenchObserver:
    def __init__(self, group_id):
        self.group_id = group_id
        self.ws = None
        self.events = []
        self.bot_finals = {}
        self._req_counter = 0
        self._pending = {}
        self._reader_task = None

    def _next_req_id(self):
        self._req_counter += 1
        return f"sv{self._req_counter:03d}"

    def expect_bot_final(self, bot_uuid):
        ev = self.bot_finals.setdefault(bot_uuid, asyncio.Event())
        return ev

    async def connect(self):
        self.ws = await websockets.connect(CLIENT_WS_URL, additional_headers=MOCK_HEADERS)
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
        event_name = frame.get("event")
        bot_uuid = frame.get("bot_uuid")
        payload = frame.get("payload") or {}
        state = payload.get("state")
        self.events.append({"event": event_name, "bot_uuid": bot_uuid, "state": state})
        if event_name in ("chat.event", "chat") and state == "final":
            msg = payload.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
            ok(f"  final [{str(bot_uuid)[:8]}]: {str(content)[:80]}")
            if bot_uuid and bot_uuid in self.bot_finals:
                self.bot_finals[bot_uuid].set()

    async def subscribe(self):
        req_id = self._next_req_id()
        fut = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self.ws.send(json.dumps({
            "type": "req", "id": req_id, "method": "connect",
            "params": {"group_id": self.group_id},
        }))
        res = await asyncio.wait_for(fut, timeout=10)
        assert res.get("ok"), f"subscribe failed: {res}"
        ok(f"Subscribed to group {self.group_id}")

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            await self.ws.close()


# ── Phases ────────────────────────────────────────────────────────────────


async def phase_create_service_group(client):
    info("Phase 1: create manager_worker group with service_spec + AntDing callback")
    # The api_key seeded in setup.sh is bound to SERVICE_GROUP_ID; we
    # must create the group with that exact id so the auth middleware
    # accepts the X-BCS-Service-Key header.
    target_group_id = os.environ.get("SERVICE_GROUP_ID", "")
    assert target_group_id, "SERVICE_GROUP_ID not exported by setup.sh"
    body = {
        "id": target_group_id,
        "driver_bot": COORD_UUID,
        "participants": [
            {"bot_uuid": COORD_UUID, "role": "manager"},
            {"bot_uuid": DBA_UUID, "role": "worker"},
            {"bot_uuid": DEVOPS_UUID, "role": "worker"},
        ],
        "group_strategy": "manager_worker",
        "service_spec": {
            "max_concurrency": 2,
            "timeout_seconds": 300,
            "callback_config": {
                "channels": [
                    {
                        "type": "antding",
                        **DEFAULT_ANTDING,
                    }
                ]
            },
        },
    }
    resp = await http_post(client, "/groups", token=COORD_TOKEN, json_body=body)
    assert resp.status_code == 200, f"create group failed: {resp.status_code} {resp.text}"
    data = resp.json()
    group_id = data.get("id") or data.get("group_id")
    assert group_id == target_group_id, (
        f"group_id mismatch: requested={target_group_id} got={group_id}"
    )
    ok(f"Service group created: {group_id}")

    # Verify service_spec persisted via GET
    g = (await http_get(client, f"/groups/{group_id}")).json()
    spec = g.get("service_spec") or {}
    assert spec.get("max_concurrency") == 2, f"max_concurrency not persisted: {spec}"
    ok(f"service_spec persisted (max_concurrency={spec.get('max_concurrency')})")
    return group_id


async def phase_invoke_via_service_api(client, group_id):
    info("Phase 2: POST /services/{group_id}/sessions (external API key auth)")
    if not SERVICE_API_KEY:
        fail_msg(
            "SERVICE_API_KEY env not set — setup.sh must export it after seeding "
            "api_keys in the BCS config (Part B Task 3)"
        )
        return None
    headers = {"X-BCS-Service-Key": SERVICE_API_KEY}
    body = {
        "input": {
            "objective": "执行数据库慢查询审计和索引优化建议",
            "expected_workers": ["DBA"],
        },
        "session_title": "DB perf audit",
        "meta": {
            "callback_target": {
                "user_id": os.environ.get("BCS_TEST_ANTDING_USER_ID", "11111111"),
            }
        },
    }
    resp = await http_post(client, f"/services/{group_id}/sessions",
                           headers=headers, json_body=body)
    assert resp.status_code == 202, f"invoke failed: {resp.status_code} {resp.text}"
    data = resp.json()
    session_id = data["session_id"]
    assert data.get("activation_count") == 1
    assert data.get("reused") is False
    assert data.get("callback_status") == "pending"
    ok(f"Invocation accepted: {session_id} (activation_count=1, reused=false)")
    return session_id


async def phase_send_user_kickoff(group_id, session_id):
    """Sends an kickoff message into the group on the service invocation's
    session_id. The manager bot is instructed to forward to DBA and call
    bcs_task_complete as soon as DBA replies (no full audit needed),
    keeping the test fast."""
    info("Phase 3: kickoff manager via workbench WS chat.send (session_id=invocation)")
    obs = WorkbenchObserver(group_id)
    try:
        await obs.connect()
        coord_final = obs.expect_bot_final(COORD_UUID)
        dba_final = obs.expect_bot_final(DBA_UUID)
        await obs.subscribe()

        # Send chat.send pinned to the service invocation session
        req_id = obs._next_req_id()
        params = {
            "sessionKey": "service-main",
            "message": (
                "向 DBA 发一条消息，收到 DBA 任何回复后立刻调用 bcs_task_complete 提交总结，不需要等完整分析结果"
            ),
            "group_id": group_id,
            "bot_uuid": COORD_UUID,
            "bot_id": COORD_UUID,
            "session_id": session_id,
            "timeoutMs": BOT_RESPONSE_TIMEOUT * 1000,
        }
        fut = asyncio.get_running_loop().create_future()
        obs._pending[req_id] = fut
        await obs.ws.send(json.dumps({
            "type": "req", "id": req_id, "method": "chat.send",
            "params": params,
        }))
        res = await asyncio.wait_for(fut, timeout=15)
        assert res.get("ok"), f"chat.send rejected: {res}"
        ok(f"chat.send accepted (runId={res.get('payload', {}).get('runId')})")

        info("Waiting for DBA to finish the assigned task...")
        try:
            await asyncio.wait_for(dba_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("DBA completed assigned task")
        except asyncio.TimeoutError:
            fail_msg(f"DBA did not complete task in {BOT_RESPONSE_TIMEOUT}s")
            return False

        info("Waiting for Coordinator to wrap up via bcs_task_complete...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator produced final after task completion")
        except asyncio.TimeoutError:
            fail_msg("Coordinator never produced final")
            return False
        return True
    finally:
        await obs.close()


async def phase_assert_callback_terminal(client, group_id, session_id):
    """Polls the service-invocation read endpoint until callback_status
    reaches a terminal state. The AntDing prod endpoint returns
    success=false for our bogus credentials, so we expect 'failed' or
    'partial_failed' (but assert only on 'in {terminal}', not on a
    specific value, so the test stays robust if real creds are
    plugged in later)."""
    info("Phase 4: poll callback_status for terminal state")
    headers = {"X-BCS-Service-Key": SERVICE_API_KEY}
    deadline = time.time() + CALLBACK_POLL_TIMEOUT
    last_status = None
    while time.time() < deadline:
        resp = await http_get(
            client,
            f"/services/{group_id}/sessions/{session_id}",
            headers=headers,
        )
        if resp.status_code != 200:
            fail_msg(f"GET invocation failed: {resp.status_code} {resp.text}")
            return False
        body = resp.json()
        last_status = body.get("callback_status")
        if last_status in ("succeeded", "partial_failed", "failed"):
            ok(f"Session.callback_status reached terminal: {last_status}")
            ok(f"  status={body.get('status')} "
               f"latest_activation_seq={body.get('latest_activation_seq')}")
            return True
        await asyncio.sleep(CALLBACK_POLL_INTERVAL)

    fail_msg(
        f"callback_status never reached terminal in {CALLBACK_POLL_TIMEOUT}s; "
        f"last={last_status}"
    )
    return False


async def main():
    if not (COORD_UUID and DBA_UUID and DEVOPS_UUID and COORD_TOKEN):
        print("Missing env vars — run via run.sh service-invoke-manager-worker")
        sys.exit(2)

    print()
    print("=" * 64)
    print("Service-Invocation Manager-Worker E2E (Part B)")
    print("=" * 64)

    failures = []
    async with httpx.AsyncClient() as client:
        group_id = await phase_create_service_group(client)
        session_id = await phase_invoke_via_service_api(client, group_id)
        if session_id is None:
            failures.append("Phase 2 (invoke)")
            print()
            fail_msg("FAILED at Phase 2 — cannot continue without session_id")
            sys.exit(1)

        if not await phase_send_user_kickoff(group_id, session_id):
            failures.append("Phase 3 (manager-worker round trip)")

        if not failures and not await phase_assert_callback_terminal(
            client, group_id, session_id
        ):
            failures.append("Phase 4 (callback dispatcher)")

    print()
    if failures:
        fail_msg(f"FAILED: {', '.join(failures)}")
        sys.exit(1)
    ok("All phases passed")


if __name__ == "__main__":
    asyncio.run(main())
