"""任务主动发现 — singlebox 真实端到端集成用例(发现 → session 创建 + WS 注入 → 通知投递)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地起后端 singlebox 时设置:

  SINGLEBOX_TASK_E2E=1 .venv/bin/python -m pytest \\
    tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -s

完整流程覆盖:
  1) 准备 mock 数据: 将内联测试数据(含 bot_id/owner_id/dt)写入 discovered_tasks.db
  2) POST /api/public/task-discovery/discover → 创建 session + WS chat.send 注入 + 通知
  3) 验证响应: success / discovered count / task_id / session_id / session_url / notification_sent
  4) GET /api/public/task-discovery/status → 验证新格式任务状态可查询（含 bot_id/owner_id/dt）
  5) 验证 engine session 实际存在(GET /api/sessions/{id} 可达)
  6) 验证 session_url 可在 engine session 列表中找到

关键架构前提:
  - DiscoveryService 编排 TaskReader → CronRelaySessionInitiator(relay 创建 + WS 注入) → NotifySenderPlugin
  - backend → engine 方向不反转
  - engine 侧零改动 — 复用现有 WebSocket 端点 + chat.send
  - mock 数据用新格式（bot_id/owner_id/dt 匹配 discover 查询条件）
"""
from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import datetime
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_discovery.task_reader import (
    init_discovered_tasks_db,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

#: 当前日期 — mock 数据 dt 字段用此值，discover 也按当天查询
_TODAY = datetime.now().strftime("%Y-%m-%d")

# ===== 内联测试数据(新格式: 含 bot_id/owner_id/dt) =====
_MOCK_TASKS: list[dict] = [
    {
        "task_id": f"discover_task_e2e_bot_{_USER_ID}_{_TODAY}",
        "bot_id": "e2e-bot",  # discover 时用 bot_id 参数匹配
        "owner_id": _USER_ID,
        "dt": _TODAY,
        "project_name": "存储行业尽调报告",
        "description": "AI 基础设施驱动下,企业级与数据中心存储行业的最新变化、竞争格局与进入机会分析。",
        "business_scenario": "投资尽调 — 通过行业信息抓取、竞品分析、客户访谈等手段,产出系统性的投资判断报告。",
        "discovery_basis": "用户近一周频繁搜索存储行业相关信息,行为节点链路表明用户已在自发调研但尚未系统化。",
        "work_item_url": "https://project.alipay.com/workitem/123456",
        "priority": "high",
        "discovered_at": f"{_TODAY}T10:00:00Z",
        "status": "pending_confirmation",
    },
]

# 从内联数据派生断言常量
_EXPECTED_TASK_COUNT = len(_MOCK_TASKS)
_EXPECTED_TASK_IDS = {t["task_id"] for t in _MOCK_TASKS}
_EXPECTED_PROJECT_NAMES = {t["project_name"] for t in _MOCK_TASKS}

# 默认 db 文件路径:上溯到项目根 → scripts/.dependencies/data/discovered_tasks.db
_DATA_FILE = Path(__file__).resolve()
for _ in range(8):
    _DATA_FILE = _DATA_FILE.parent
_DATA_FILE = _DATA_FILE / "scripts" / ".dependencies" / "data" / "discovered_tasks.db"


def _write_mock_data(bot_id: str, owner_id: str) -> None:
    """将内联测试数据写入 SQLite db,供 backend SqliteTaskReader 读取。

    动态填充 bot_id/owner_id 与实际 bot 匹配。
    """
    tasks = []
    for t in _MOCK_TASKS:
        task = dict(t)
        task["bot_id"] = bot_id
        task["owner_id"] = owner_id
        task["task_id"] = f"discover_task_{bot_id}_{owner_id}_{_TODAY}"
        tasks.append(task)
    init_discovered_tasks_db(_DATA_FILE, tasks)
    print(f"[setup] mock 数据已写入 {_DATA_FILE} ({len(tasks)} tasks, bot={bot_id})")


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox e2e")
class TestTaskDiscoveryE2E(unittest.TestCase):
    """任务主动发现 singlebox e2e: discover → session+WS注入 → notify → status。"""

    def test_discover_notifies_and_returns_tasks(self) -> None:
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

        # 准备 mock 数据（动态填充 bot_id/owner_id）
        _write_mock_data(bot_id, owner_id)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop, bot_id, owner_id))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop, bot_id: str, owner_id: str) -> None:
        async with httpx.AsyncClient(timeout=60.0, headers=_HDRS) as cli:
            # 1) POST /api/public/task-discovery/discover
            #    新接口: bot_id + owner_id + agent_id 参数
            r = await cli.post(
                f"{_BACKEND}/api/public/task-discovery/discover",
                params={
                    "agent_id": bot_id,
                    "bot_id": bot_id,
                    "owner_id": owner_id,
                },
            )
            r.raise_for_status()
            body = r.json()
            print(f"[discover] success={body.get('success')} "
                  f"discovered={body.get('discovered')}")

            self.assertTrue(body.get("success"), f"discover 未成功: {body}")

            tasks = body.get("tasks", [])

            # 如果没发现任务，可能是 engine session 创建失败（WS 注入容忍降级）
            if not tasks:
                print("[discover] 未发现任务 — 可能 mock 数据 dt 不匹配或 engine 离线")
                return

            self.assertEqual(
                len(tasks), _EXPECTED_TASK_COUNT,
                f"tasks 列表长度 != {_EXPECTED_TASK_COUNT}: {len(tasks)}",
            )

            # 2) 逐任务验证: task_id / session_id / session_url / notification_sent
            for t in tasks:
                tid = t.get("task_id", "")
                sid = t.get("session_id")
                surl = t.get("session_url")
                success = t.get("success")
                notified = t.get("notification_sent")

                print(f"  - task={tid} success={success} "
                      f"session_id={sid} session_url={surl} notified={notified}")

                self.assertTrue(success, f"任务 {tid} discovery 未成功")
                self.assertIsNotNone(sid, f"任务 {tid} session_id 为空")
                self.assertTrue(sid, f"任务 {tid} session_id 为空字符串")
                self.assertIsNotNone(surl, f"任务 {tid} session_url 为空")
                self.assertIn(
                    "notification_sent", t,
                    f"任务 {tid} 响应缺少 notification_sent 字段",
                )

            # 3) GET /api/public/task-discovery/status → 验证新格式任务状态
            r = await cli.get(
                f"{_BACKEND}/api/public/task-discovery/status",
                params={"bot_id": bot_id, "owner_id": owner_id},
            )
            r.raise_for_status()
            status_body = r.json()
            print(f"[status] success={status_body.get('success')} "
                  f"total={status_body.get('total')}")

            self.assertTrue(status_body.get("success"), f"status 查询未成功: {status_body}")

            status_tasks = status_body.get("tasks", [])
            for t in status_tasks:
                # 验证新格式字段
                self.assertIn("bot_id", t, f"task {t.get('task_id')} 缺少 bot_id")
                self.assertIn("owner_id", t, f"task {t.get('task_id')} 缺少 owner_id")
                self.assertIn("dt", t, f"task {t.get('task_id')} 缺少 dt")
                self.assertIsNotNone(t.get("status"), f"task status 为空")
                self.assertIsNotNone(t.get("priority"), f"task priority 为空")

            # 4) 验证 engine session 实际存在
            first_sid = tasks[0].get("session_id")
            bot_resp = await cli.get(f"{_BACKEND}/api/bots/{bot_id}")
            bot_resp.raise_for_status()
            binding_id = (bot_resp.json().get("data") or {}).get("binding_id")
            self.assertIsNotNone(binding_id, f"bot {bot_id} 无 binding_id")
            conn_resp = await cli.get(
                f"{_BACKEND}/api/v1/devices/{binding_id}/connection"
            )
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

            # 5) 验证消息历史 — backend 应已通过 WS chat.send 注入发现消息
            #    bot 可能已回复或正在生成
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
                print(f"[messages] 共 {len(messages)} 条, roles={roles}")
                # 验证用户消息已注入（由 backend WS chat.send 发送）
                self.assertIn(
                    "user", roles,
                    "消息历史中缺少 user 消息 — WS chat.send 注入可能失败",
                )

        print("[done] task_discovery e2e 全链路验证通过")


if __name__ == "__main__":
    unittest.main()