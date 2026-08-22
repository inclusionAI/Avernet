"""任务主动发现 — singlebox 真实端到端集成用例(发现 → session 创建 → 通知投递 → 发送任务给大模型)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地起后端 singlebox 时设置:

  SINGLEBOX_TASK_E2E=1 .venv/bin/python -m pytest \
    tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -s

完整流程覆盖:
  1) 准备 mock 数据: 将内联测试数据写入 scripts/.dependencies/data/discovered_tasks.db
  2) provisioning: 建一个 test agent bot(获取真实 agent_id)
  3) POST /openapi/v1/collaboration/tasks/discovery/discover   → 读取 mock 任务 + 创建 engine session + 投递通知
  4) 验证响应: code=200000 / discovered count / task_id / session_id / notification_sent
  5) GET /openapi/v1/collaboration/tasks/discovery/status      → 验证任务状态可查询
  6) 验证 engine session 实际存在(GET /api/sessions/{id} 可达)
  7) WebSocket 连接 engine,将发现的任务内容发送给大模型,验证回复

关键架构前提:
  - DiscoveryService 编排 TaskReader(读 SQLite db)→ SessionCreator(调 engine POST /api/sessions)→ NotifySenderPlugin(投递通知)
  - 每个 pending_confirmation 任务 → 一个 engine session + 通知投递(供用户前端确认)
  - session_url 不在 discover 阶段构建 — 用户 bot 没有单独的 session_url
  - 任务执行: 本测试通过 WebSocket 向 engine session 发送任务内容,验证大模型可响应
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_discovery.task_reader import (
    init_discovered_tasks_db,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

# ===== 内联测试数据(运行前写入 scripts/.dependencies/data/discovered_tasks.db)=====
# 参考 test_task_integration_e2e.py 的 _execute_body() / ROLE_BOTS 模式:
# 测试数据固定在脚本内,不依赖外部文件。
_MOCK_TASKS: list[dict] = [
    {
        "task_id": "disc-e2e-001",
        "project_name": "存储行业尽调报告",
        "description": "AI 基础设施驱动下,企业级与数据中心存储行业的最新变化、竞争格局与进入机会分析。",
        "business_scenario": "投资尽调 — 通过行业信息抓取、竞品分析、客户访谈等手段,产出系统性的投资判断报告。",
        "discovery_basis": "用户近一周频繁搜索存储行业相关信息,行为节点链路表明用户已在自发调研但尚未系统化。",
        "work_item_url": "https://project.alipay.com/workitem/123456",
        "priority": "high",
        "discovered_at": "2026-08-17T10:00:00Z",
        "status": "pending_confirmation",
    },
]

# 从内联数据派生断言常量
_EXPECTED_TASK_COUNT = len(_MOCK_TASKS)
_EXPECTED_TASK_IDS = {t["task_id"] for t in _MOCK_TASKS}
_EXPECTED_PROJECT_NAMES = {t["project_name"] for t in _MOCK_TASKS}

# 默认 db 文件路径:上溯到项目根 → scripts/.dependencies/data/discovered_tasks.db
# 测试文件在 src/backend/tests/community/core/task/singlebox_e2e/ → 距项目根 8 级
# router.py 在 src/backend/src/agentclaw/community/adapters/http/task_discovery/ → 距项目根 9 级
_DATA_FILE = Path(__file__).resolve()
for _ in range(8):
    _DATA_FILE = _DATA_FILE.parent
_DATA_FILE = _DATA_FILE / "scripts" / ".dependencies" / "data" / "discovered_tasks.db"


def _write_mock_data() -> None:
    """将内联测试数据写入 SQLite db,供 backend SqliteTaskReader 读取。"""
    init_discovered_tasks_db(_DATA_FILE, _MOCK_TASKS)
    print(f"[setup] mock 数据已写入 {_DATA_FILE} ({len(_MOCK_TASKS)} tasks)")


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox e2e")
class TestTaskDiscoveryE2E(unittest.TestCase):
    """任务主动发现 singlebox e2e: discover → session → notify → status。"""

    def test_discover_notifies_and_returns_tasks(self) -> None:
        # 准备 mock 数据
        _write_mock_data()

        # 同步查已有 bot（skipTest 在 event loop 内会被吞，所以放在外面）
        with httpx.Client(timeout=30.0, headers=_HDRS) as cli:
            bots: list[dict] = []
            for endpoint in [
                f"{_BACKEND}/api/bots/by-owner-or-collaborator",
                f"{_BACKEND}/api/bots",
            ]:
                try:
                    r = cli.get(endpoint, params={"user_id": _USER_ID})
                    if r.status_code == 200:
                        bots = (r.json().get("data") or {}).get("items") or []
                        if bots:
                            break
                except Exception:
                    continue
        if not bots:
            self.skipTest("singlebox 未 provision 任何 bot,请先 start all")
        bot = bots[0]
        bot_id = bot["bot_id"]
        owner_id = bot.get("owner_id", _USER_ID)
        print(f"[bot] 使用已有 bot: bot_id={bot_id} owner_id={owner_id}")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop, bot_id, owner_id))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop, bot_id: str, owner_id: str) -> None:
        async with httpx.AsyncClient(timeout=60.0, headers=_HDRS) as cli:
            # POST /openapi/v1/collaboration/tasks/discovery/discover
            #    传 bot_id + owner_id: 定位到 per-bot engine 直连创建 session
            r = await cli.post(
                f"{_BACKEND}/openapi/v1/collaboration/tasks/discovery/discover",
                params={
                    "user_id": _USER_ID,
                    "agent_id": bot_id,
                    "bot_id": bot_id,
                    "owner_id": owner_id,
                },
            )
            r.raise_for_status()
            body = r.json()
            data = body.get("data") or {}
            print(f"[discover] code={body.get('code')} "
                  f"discovered={data.get('discovered')}")

            self.assertEqual(body.get("code"), 200000, f"discover 未成功: {body}")
            self.assertEqual(
                data.get("discovered"), _EXPECTED_TASK_COUNT,
                f"discovered 数量 != {_EXPECTED_TASK_COUNT}: {data.get('discovered')}",
            )

            tasks = data.get("tasks", [])
            self.assertEqual(len(tasks), _EXPECTED_TASK_COUNT, "tasks 列表长度不匹配")

            # 4) 逐任务验证: task_id / session_id / notification_sent
            discovered_ids: set[str] = set()
            for t in tasks:
                tid = t.get("task_id", "")
                sid = t.get("session_id")
                success = t.get("success")
                notified = t.get("notification_sent")

                print(f"  - task={tid} success={success} "
                      f"session_id={sid} notified={notified}")
                discovered_ids.add(tid)

                self.assertTrue(success, f"任务 {tid} discovery 未成功")
                self.assertIsNotNone(sid, f"任务 {tid} session_id 为空")
                self.assertTrue(sid, f"任务 {tid} session_id 为空字符串")
                self.assertIn(
                    "notification_sent", t,
                    f"任务 {tid} 响应缺少 notification_sent 字段",
                )
                self.assertTrue(
                    notified,
                    f"任务 {tid} 通知未发送 (notification_sent={notified})",
                )

            # task_id 集合与内联 mock 数据一致
            self.assertEqual(
                discovered_ids, _EXPECTED_TASK_IDS,
                f"发现 task_id 集合不匹配: {discovered_ids} vs {_EXPECTED_TASK_IDS}",
            )

            # 5) GET /openapi/v1/collaboration/tasks/discovery/status → 验证任务状态可查
            r = await cli.get(f"{_BACKEND}/openapi/v1/collaboration/tasks/discovery/status")
            r.raise_for_status()
            status_body = r.json()
            status_data = status_body.get("data") or {}
            print(f"[status] code={status_body.get('code')} "
                  f"total={status_data.get('total')}")

            self.assertEqual(status_body.get("code"), 200000, f"status 查询未成功: {status_body}")
            self.assertEqual(
                status_data.get("total"), _EXPECTED_TASK_COUNT,
                f"status total != {_EXPECTED_TASK_COUNT}",
            )

            status_tasks = status_data.get("tasks", [])
            status_ids = {t.get("task_id") for t in status_tasks}
            self.assertEqual(
                status_ids, _EXPECTED_TASK_IDS,
                f"status task_id 集合不匹配: {status_ids} vs {_EXPECTED_TASK_IDS}",
            )
            for t in status_tasks:
                self.assertIn(
                    t.get("project_name"), _EXPECTED_PROJECT_NAMES,
                    f"task {t.get('task_id')} project_name 不在预期集合内",
                )
                self.assertIsNotNone(t.get("status"), f"task {t.get('task_id')} status 为空")
                self.assertIsNotNone(t.get("priority"), f"task {t.get('task_id')} priority 为空")

            # 6) 验证 engine session 实际存在
            #    链路同 singlebox_engine_adapter._resolve_target():
            #    GET /api/bots/{bot_id} → binding_id → GET /api/v1/devices/{binding_id}/connection
            first_sid = tasks[0].get("session_id")
            bot_resp = await cli.get(f"{_BACKEND}/api/bots/{bot_id}")
            bot_resp.raise_for_status()
            binding_id = (bot_resp.json().get("data") or {}).get("binding_id")
            self.assertIsNotNone(binding_id, f"bot {bot_id} 无 binding_id")
            conn_resp = await cli.get(f"{_BACKEND}/api/v1/devices/{binding_id}/connection")
            conn_resp.raise_for_status()
            target = (conn_resp.json().get("data") or {}).get("target") or ""
            self.assertTrue(target, f"未取到 engine target: {conn_resp.json()}")
            print(f"[engine] target={target} (binding_id={binding_id})")

            eng_resp = await cli.get(
                f"http://{target}/api/sessions",
                params={"limit": 100, "offset": 0},
                headers={"x-user-id": _USER_ID},
            )
            eng_resp.raise_for_status()
            eng_sessions = eng_resp.json().get("data") or []
            found = any(
                first_sid in (s.get("id") or s.get("session_id") or "")
                for s in eng_sessions
            )
            print(f"[engine] {target} 返回 {len(eng_sessions)} 条, "
                  f"first_sid={first_sid} found={found}")
            self.assertTrue(
                found,
                f"session {first_sid} 未在 per-bot engine({target})中找到",
            )

            # 7) WebSocket 连接 engine,将发现的任务内容发送给大模型
            #    协议同 singlebox_engine_adapter._ws_chat_roundtrip():
            #    connect(proto3 握手) → chat.send → 收到 state=final 事件
            task = _MOCK_TASKS[0]
            task_message = (
                f"请帮我处理以下任务:\n"
                f"项目名称: {task['project_name']}\n"
                f"任务描述: {task['description']}\n"
                f"业务场景: {task['business_scenario']}\n"
                f"优先级: {task['priority']}"
            )
            reply = await self._ws_chat(target, first_sid, task_message)
            print(f"[chat] 大模型回复: {reply}")
            self.assertTrue(reply, "大模型回复为空")
            self.assertNotIn("[错误]", reply, f"大模型回复包含错误: {reply}")
            self.assertNotIn("[超时]", reply, f"大模型回复超时: {reply}")

            # 8) 回查消息历史确认 assistant 记录存在
            #    引擎可能将用户消息归为 user/tool_result,只断言 assistant 存在。
            import base64
            encoded_id = base64.urlsafe_b64encode(first_sid.encode()).decode()
            msg_resp = await cli.get(
                f"http://{target}/api/sessions/{encoded_id}/messages",
                params={"limit": 10, "offset": 0},
                headers={"x-user-id": _USER_ID},
            )
            if msg_resp.status_code == 200:
                messages = msg_resp.json().get("data") or []
                roles = [m.get("role") for m in messages]
                self.assertIn("assistant", roles, "消息历史中缺少 assistant 消息")
                print(f"[messages] 共 {len(messages)} 条, roles={roles}")

        print("[done] task_discovery e2e 全链路验证通过")

    async def _ws_chat(self, target: str, session_key: str, message: str) -> str:
        """开 WebSocket:connect 握手 → chat.send → 读到 final → 返回回复文本。

        协议同 singlebox_engine_adapter._ws_chat_roundtrip()。
        """
        import websockets

        ws_path = "/api/openclaw/ws"
        uri = f"ws://{target}{ws_path}"
        connect_params = {
            "minProtocol": 3,
            "maxProtocol": 3,
            "client": {"id": "task-discovery-e2e", "version": "1.0.0", "platform": "linux", "mode": "operator"},
            "role": "operator",
        }

        async with websockets.connect(uri, open_timeout=10) as ws:
            # 1) 握手
            await ws.send(json.dumps({
                "type": "req", "id": "1", "method": "connect", "params": connect_params,
            }))
            hs = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if not hs.get("ok"):
                return f"[握手失败] {json.dumps(hs)[:200]}"

            # 2) 发消息
            await ws.send(json.dumps({
                "type": "req", "id": "2", "method": "chat.send",
                "params": {"sessionKey": session_key, "message": message},
            }))
            ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if not ack.get("ok"):
                return f"[发送被拒绝] {json.dumps(ack)[:200]}"

            # 3) 读事件到 final
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    return "[超时] 60秒内未收到回复"
                data = json.loads(raw)
                if data.get("type") != "event" or data.get("event") != "chat":
                    continue
                payload = data.get("payload") or {}
                state = payload.get("state")
                if state == "final":
                    message_obj = payload.get("message") or {}
                    contents = message_obj.get("content") or []
                    texts = [
                        c.get("text", "") for c in contents
                        if isinstance(c, dict) and c.get("type") == "text"
                    ]
                    return "\n".join(texts) if texts else json.dumps(payload, ensure_ascii=False)[:500]
                if state == "error":
                    return f"[错误] {payload.get('errorMessage', 'unknown')}"


if __name__ == "__main__":
    unittest.main()
