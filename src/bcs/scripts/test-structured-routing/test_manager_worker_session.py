#!/usr/bin/env python3
"""
E2E test for Master-Slave mode with session-aware routing (Task 14).

Walks the full chain:
  1. POST /groups            → create a group (default strategy: chat)
  2. PATCH /groups/{id}/settings → set group_strategy = manager_worker
  3. GET /groups/{id}        → verify persistence
  4. POST /groups/{id}/sessions → create a session
  5. WS /ws (workbench)      → connect + subscribe
  6. chat.send with session_id → coordinator uses bcs_assign_task
  7. GET session messages    → query both Manager and Worker session views
  8. GET /bots/{coord}/groups + /groups/{id}/sessions → bot 视角列表查询
  9. PATCH /sessions/{sid}/members/{human} mode=present → Human first-insert
 10. GET /bots/{human}/groups + /groups/{id}/sessions → Human 视角 union 命中

Note: In manager_worker mode, the BCN plugin hides bcs_route and only
exposes bcs_assign_task / bcs_task_complete. The test focuses on task
dispatch (bcs_assign_task) which exercises the handle_manager_worker_event
code path in BCS's WS dispatcher.

Setup.sh must have been run first.
Usage:
    source <(bash setup.sh | grep -E '^[A-Z_]+=')  # or via run.sh session-manager-worker
    python3 test_manager_worker_session.py
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

BOT_RESPONSE_TIMEOUT = 120  # LLM inference can be slow


def ok(msg):
    print(f"  {PASS} {msg}")


def fail_msg(msg):
    print(f"  {FAIL} {msg}")


def info(msg):
    print(f"  {CYAN}→{NC} {msg}")


def dim(msg):
    print(f"  {GRAY}{msg}{NC}")


# ── HTTP helpers ────────────────────────────────────────────────────────────


async def http_post(client, path, *, token, json_body=None):
    h = {"Authorization": f"Bearer {token}"}
    return await client.post(f"{BCS_URL}{path}", json=json_body or {}, headers=h, timeout=10)


async def http_patch(client, path, *, token, json_body):
    h = {"Authorization": f"Bearer {token}"}
    return await client.patch(f"{BCS_URL}{path}", json=json_body, headers=h, timeout=10)


async def create_group(client, driver, participants, *, group_strategy=None):
    body = {"driver_bot": driver, "participants": participants}
    if group_strategy:
        body["group_strategy"] = group_strategy
    resp = await http_post(client, "/groups", token=COORD_TOKEN, json_body=body)
    assert resp.status_code == 200, f"create group failed: {resp.status_code} {resp.text}"
    data = resp.json()
    return data.get("id") or data.get("group_id")


async def patch_group(client, group_id, patch_body):
    resp = await http_patch(
        client, f"/groups/{group_id}/settings", token=COORD_TOKEN,
        json_body=patch_body,
    )
    assert resp.status_code == 200, f"patch group failed: {resp.status_code} {resp.text}"
    return resp.json()


async def get_group(client, group_id):
    resp = await client.get(f"{BCS_URL}/groups/{group_id}", timeout=10)
    assert resp.status_code == 200, f"get group failed: {resp.status_code} {resp.text}"
    return resp.json()


async def create_session(client, group_id):
    resp = await http_post(
        client, f"/groups/{group_id}/sessions", token=COORD_TOKEN,
        json_body={"session_kind": "chat"},
    )
    assert resp.status_code == 201, f"create session failed: {resp.status_code} {resp.text}"
    data = resp.json()
    sid = data.get("session_id")
    assert sid, f"session response missing session_id: {data}"
    return sid


# ── Workbench WS client (observer + sender) ────────────────────────────────


class WorkbenchObserver:
    """Connects to BCS /ws, subscribes to a group, and sends chat.send."""

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
        return f"ms{self._req_counter:03d}"

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
        self.events.append({"event": event_name, "bot_uuid": bot_uuid, "state": state, "raw": frame})

        if event_name in ("chat.event", "chat"):
            if state == "delta":
                msg = payload.get("message", {})
                text = ""
                for block in (msg.get("content") or []):
                    if isinstance(block, dict):
                        text += block.get("text", "")
                if text:
                    dim(f"  Δ [{str(bot_uuid)[:8]}]: {text[:60]}")
            elif state == "final":
                msg = payload.get("message", {})
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = "".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in content
                    )
                ok(f"  final [{str(bot_uuid)[:8]}]: {str(content)[:80]}")
                if bot_uuid and bot_uuid in self.bot_finals:
                    self.bot_finals[bot_uuid].set()
        elif event_name == "agent":
            data = payload.get("data", {})
            stream = payload.get("stream")
            if stream == "tool":
                phase = data.get("phase", "?")
                tool_name = data.get("name", "?")
                dim(f"  🔧 {phase}: {tool_name}")

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
            "type": "req", "id": req_id, "method": "chat.send",
            "params": params,
        }))
        res = await asyncio.wait_for(fut, timeout=10)
        return res

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            await self.ws.close()


# ── Test phases ─────────────────────────────────────────────────────────────


async def phase_create_and_patch(client):
    info("Phase 1: create manager_worker group directly")
    participants = [
        {"bot_uuid": COORD_UUID, "role": "manager"},
        {"bot_uuid": DBA_UUID, "role": "worker"},
        {"bot_uuid": DEVOPS_UUID, "role": "worker"},
    ]
    # Create directly with manager_worker strategy so the initial system
    # context injected to the coordinator uses manager_worker_initial_message
    # (bcs_assign_task instructions) instead of initial_group_context_message
    # (bcs_route instructions).
    group_id = await create_group(client, COORD_UUID, participants, group_strategy="manager_worker")
    ok(f"Group created: {group_id} (strategy=manager_worker)")

    # Verify persistence via GET
    g = await get_group(client, group_id)
    strategy = g.get("group_strategy")
    assert strategy == "manager_worker", f"group_strategy not persisted: {strategy}"
    ok(f"group_strategy = {strategy} confirmed via GET")

    return group_id


async def phase_create_session(client, group_id):
    info("Phase 2: create session under manager_worker group")
    session_id = await create_session(client, group_id)
    ok(f"Session created: {session_id}")
    return session_id


async def phase_task_dispatch_with_session(group_id, session_id):
    """
    Phase 3: chat.send with session_id + bcs_assign_task.

    This triggers the manager-worker specific path: the coordinator uses
    bcs_assign_task to dispatch a task to DBA. BCS should route through
    handle_manager_worker_event → task channel.

    In manager_worker mode the BCN plugin hides bcs_route — the only routing
    tool available is bcs_assign_task.
    """
    info("Phase 3: task dispatch (bcs_assign_task) with session_id")
    obs = WorkbenchObserver(group_id)
    try:
        await obs.connect()

        coord_final = obs.expect_bot_final(COORD_UUID)
        dba_final = obs.expect_bot_final(DBA_UUID)

        await obs.subscribe()

        # Wait for coordinator to finish any pending work
        info("Waiting for Coordinator to settle...")
        try:
            await asyncio.wait_for(coord_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator settled")
        except asyncio.TimeoutError:
            dim("Coordinator did not settle (may already be idle)")

        info("Sending chat.send → coordinator should bcs_assign_task to DBA")
        res = await obs.send(
            "我需要让 DBA 独立执行一次完整的数据库性能审计，请把这项任务指派给 DBA，任务描述为 '执行数据库慢查询审计和索引优化建议'",
            bot_uuid=COORD_UUID,
            session_id=session_id,
        )
        assert res.get("ok"), f"chat.send rejected: {res}"
        ok(f"chat.send accepted (runId={res.get('payload', {}).get('runId')})")

        # Re-arm for coordinator post-task-dispatch response
        coord_final2 = obs.expect_bot_final(COORD_UUID)

        info("Waiting for DBA to complete the assigned task...")
        try:
            await asyncio.wait_for(dba_final.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("DBA completed assigned task")
        except asyncio.TimeoutError:
            fail_msg(f"DBA did not complete task in {BOT_RESPONSE_TIMEOUT}s")
            return False

        # Coordinator should also produce a final after receiving DBA's result
        try:
            await asyncio.wait_for(coord_final2.wait(), timeout=BOT_RESPONSE_TIMEOUT)
            ok("Coordinator received task result and responded")
        except asyncio.TimeoutError:
            dim("Coordinator did not produce final after task completion")

        return True
    finally:
        await obs.close()


async def phase_query_session_messages(group_id, session_id):
    """Phase 4: query session messages for both Manager and Worker bots."""
    info("Phase 4: query session messages (Manager and Worker)")
    async with httpx.AsyncClient() as client:
        # Manager (coordinator) messages
        resp = await client.get(
            f"{BCS_URL}/sessions/{session_id}/messages",
            headers={"Authorization": f"Bearer {COORD_TOKEN}"},
            timeout=15,
        )
        if resp.status_code == 200:
            msgs = resp.json()
            print(f"\n  ┌─ Manager ({COORD_UUID}) session messages ({len(msgs)} total)")
            for m in msgs:
                sender = m.get("sender", "?")
                role = m.get("role", "?")
                content = m.get("content", "")
                # Truncate long content
                preview = content[:200] + "..." if len(content) > 200 else content
                print(f"  │ [{role}] {sender}: {preview}")
            print(f"  └─ end")
            ok(f"Manager messages: {len(msgs)}")
        else:
            fail_msg(f"Manager messages failed: {resp.status_code} {resp.text}")
            return False

        print()

        # Worker (DBA) messages
        resp = await client.get(
            f"{BCS_URL}/sessions/{session_id}/messages",
            params={"view_bot_id": DBA_UUID},
            headers={"Authorization": f"Bearer {COORD_TOKEN}"},
            timeout=15,
        )
        if resp.status_code == 200:
            msgs = resp.json()
            print(f"  ┌─ Worker ({DBA_UUID}) session messages ({len(msgs)} total)")
            for m in msgs:
                sender = m.get("sender", "?")
                role = m.get("role", "?")
                content = m.get("content", "")
                preview = content[:200] + "..." if len(content) > 200 else content
                print(f"  │ [{role}] {sender}: {preview}")
            print(f"  └─ end")
            ok(f"Worker messages: {len(msgs)}")
        else:
            fail_msg(f"Worker messages failed: {resp.status_code} {resp.text}")
            return False

        return True


async def phase_bot_view_listings(group_id, session_id):
    """Phase 5: bot 视角查 group 列表 + session 列表。

    用 manager (Coordinator) bot 的 token 查：
      - GET /bots/{COORD_UUID}/groups → 必须命中刚建的 group
      - GET /groups/{group_id}/sessions → 必须命中刚建的 session
    """
    info("Phase 5: bot 视角列表查询 (Coordinator)")
    coord_headers = {"Authorization": f"Bearer {COORD_TOKEN}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{BCS_URL}/bots/{COORD_UUID}/groups",
            headers=coord_headers,
            timeout=15,
        )
        if resp.status_code != 200:
            fail_msg(f"GET /bots/{{coord}}/groups failed: {resp.status_code} {resp.text}")
            return False
        items = resp.json().get("items", [])
        gids = [g.get("group_id") for g in items]
        if group_id not in gids:
            fail_msg(f"bot's groups missing target group_id={group_id}; got={gids}")
            return False
        ok(f"GET /bots/coord/groups 命中 group_id={group_id} (total={len(gids)})")

        resp = await client.get(
            f"{BCS_URL}/groups/{group_id}/sessions",
            headers=coord_headers,
            timeout=15,
        )
        if resp.status_code != 200:
            fail_msg(f"GET /groups/{{id}}/sessions failed: {resp.status_code} {resp.text}")
            return False
        sessions = resp.json().get("items", [])
        sids = [s.get("session_id") for s in sessions]
        if session_id not in sids:
            fail_msg(f"bot's sessions missing target session_id={session_id}; got={sids}")
            return False
        ok(f"GET /groups/{{id}}/sessions 命中 session_id={session_id} (total={len(sids)})")

        return True


async def phase_human_join_and_query(group_id, session_id):
    """Phase 6: Human 通过 PATCH /sessions/{sid}/members/{aid} 加入 session
    (session-only 临时参与者)，然后用 Human 视角查能命中。

    步骤：
      1. PATCH /sessions/{sid}/members/{human_uuid} mode=present (first-insert)
      2. GET /bots/{human_uuid}/groups → 必须命中 group_id (union session 维度)
      3. GET /groups/{group_id}/sessions → 必须命中 session_id (临时参与者过滤)
      4. GET /sessions/{sid} → 200 (caller 在 session.participants 里)

    Human 通过 BCS_AUTH_MOCK 的 X-Mock-User-Id 走 cookie 路径，无需 token。
    """
    info("Phase 6: Human 通过 session 加入 + Human 视角查询")
    human_uuid = f"human_{MOCK_USER_ID}"
    async with httpx.AsyncClient() as client:
        # Step 1: PATCH session member (first-insert Human)
        resp = await client.patch(
            f"{BCS_URL}/sessions/{session_id}/members/{human_uuid}",
            json={"mode": "present"},
            headers=MOCK_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            fail_msg(
                f"PATCH /sessions/{{sid}}/members/{human_uuid} failed: "
                f"{resp.status_code} {resp.text}"
            )
            return False
        sess = resp.json()
        members = [p.get("bot_uuid") for p in sess.get("participants", [])]
        if human_uuid not in members:
            fail_msg(f"Human first-insert returned 200 but participants 未含 {human_uuid}: {members}")
            return False
        ok(f"Human {human_uuid} first-insert 成功 (session.participants 已包含)")

        # Step 2: Human 视角查 group 列表 (走 union session 维度)
        resp = await client.get(
            f"{BCS_URL}/bots/{human_uuid}/groups",
            headers=MOCK_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            fail_msg(f"GET /bots/human/groups failed: {resp.status_code} {resp.text}")
            return False
        items = resp.json().get("items", [])
        gids = [g.get("group_id") for g in items]
        if group_id not in gids:
            fail_msg(
                f"Human 视角的 groups 未命中 {group_id} (临时参与者 union 没生效): got={gids}"
            )
            return False
        ok(f"Human 视角 GET /bots/human/groups 命中 {group_id} (total={len(gids)})")

        # Step 3: Human 视角查 session 列表 (临时参与者过滤生效，应该只看到自己加入的)
        resp = await client.get(
            f"{BCS_URL}/groups/{group_id}/sessions",
            headers=MOCK_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            fail_msg(f"GET /groups/{{id}}/sessions failed: {resp.status_code} {resp.text}")
            return False
        sessions = resp.json().get("items", [])
        sids = [s.get("session_id") for s in sessions]
        if session_id not in sids:
            fail_msg(f"Human 视角 sessions 未命中 {session_id}: got={sids}")
            return False
        ok(f"Human 视角 GET /groups/{{id}}/sessions 命中 {session_id} (visible={len(sids)})")

        # Step 4: Human 视角拿 session 详情
        resp = await client.get(
            f"{BCS_URL}/sessions/{session_id}",
            headers=MOCK_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            fail_msg(f"GET /sessions/{{sid}} failed for Human caller: {resp.status_code} {resp.text}")
            return False
        ok(f"Human 视角 GET /sessions/{{sid}} 鉴权通过 (200)")

        return True


async def main():
    if not (COORD_UUID and DBA_UUID and DEVOPS_UUID and COORD_TOKEN):
        print(
            "Missing env vars — run via run.sh session-manager-worker"
        )
        sys.exit(2)

    print()
    print("=" * 64)
    print("Manager-Worker Session-Aware Routing E2E")
    print("=" * 64)

    failures = []
    async with httpx.AsyncClient() as client:
        # Phase 1
        group_id = await phase_create_and_patch(client)

        # Phase 2
        session_id = await phase_create_session(client, group_id)

        # Phase 3: task dispatch with session_id (bcs_assign_task)
        if not await phase_task_dispatch_with_session(group_id, session_id):
            failures.append("Phase 3 (task dispatch + session_id)")

        # Phase 4: query session messages
        if not await phase_query_session_messages(group_id, session_id):
            failures.append("Phase 4 (query session messages)")

        # Phase 5: bot 视角查 group 列表 + session 列表
        if not await phase_bot_view_listings(group_id, session_id):
            failures.append("Phase 5 (bot 视角列表查询)")

        # Phase 6: Human 通过 session 加入后用 Human 视角查
        if not await phase_human_join_and_query(group_id, session_id):
            failures.append("Phase 6 (Human 通过 session 加入 + 视角查询)")

    print()
    if failures:
        fail_msg(f"FAILED: {', '.join(failures)}")
        sys.exit(1)
    ok("All phases passed")


if __name__ == "__main__":
    asyncio.run(main())
