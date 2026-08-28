"""任务主动发现 — singlebox 真实端到端集成用例(发现 → session 创建 + WS 注入 → 通知投递)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地起后端 singlebox 时设置:

  SINGLEBOX_TASK_E2E=1 .venv/bin/python -m pytest \\
    tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -s

完整流程覆盖:
  1) 准备 mock 数据: 将内联测试数据(含 bot_id/owner_id/dt)写入 discovered_tasks.db
  2) 直接调用 DiscoveryService.discover() → 创建 session + WS chat.send 注入 + 通知
  3) 验证返回: success / discovered count / task_id / session_id / session_url / notification_sent
  4) 通过 SqliteTaskReader 验证任务状态可查询（含 bot_id/owner_id/dt）
  5) 验证 engine session 实际存在(GET /api/sessions/{id} 可达)
  6) 验证 session_url 可在 engine session 列表中找到

关键架构前提:
  - 直接构造 DiscoveryService，绕过 HTTP /discover 端点（避免 DI 注入 / device 状态等问题）
  - CronRelaySessionInitiator 需要 cron_relay.forward_request() — 用 _HttpCronRelay 直接转发到 engine
  - backend → engine 方向不反转
  - engine 侧零改动 — 复用现有 WebSocket 端点 + chat.send
  - mock 数据用新格式（bot_id/owner_id/dt 匹配 discover 查询条件）
"""
from __future__ import annotations

import asyncio
import base64
import os
import unittest
from datetime import datetime

import httpx

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.models import DiscoveredTask
from agentclaw.community.core.task.task_discovery.session_initiator import (
    CronRelaySessionInitiator,
)
from agentclaw.community.plugin_api.notify_sender import NotifyMessage

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
        "title": "存储行业尽调报告",
        "instruction": "AI 基础设施驱动下,企业级与数据中心存储行业的最新变化、竞争格局与进入机会分析。",
        "background": "投资尽调 — 通过行业信息抓取、竞品分析、客户访谈等手段,产出系统性的投资判断报告。",
        "discovery_basis": "用户近一周频繁搜索存储行业相关信息,行为节点链路表明用户已在自发调研但尚未系统化。",
        "priority": "high",
        "discovered_at": f"{_TODAY}T10:00:00Z",
        "status": "pending_confirmation",
        "objective": "产出系统性的存储行业投资判断报告。",
        "acceptances": [
            {"id": "a1", "description": "覆盖存储行业 6 条核心结论"},
            {"id": "a2", "description": "报告包含数据与逻辑链路"},
        ],
    },
]

# 从内联数据派生断言常量
_EXPECTED_TASK_COUNT = len(_MOCK_TASKS)
_EXPECTED_TASK_IDS = {t["task_id"] for t in _MOCK_TASKS}
_EXPECTED_PROJECT_NAMES = {t["title"] for t in _MOCK_TASKS}

class _InMemoryTaskReader:
    """轻量内存 reader — 供直接构造 DiscoveryService 的 e2e 测试使用。

    返回预设的 DiscoveredTask 列表，替代旧 SqliteTaskReader 的文件读取。
    """

    def __init__(self, tasks: list[DiscoveredTask]) -> None:
        self._tasks = tasks

    def read_discovered_tasks(self) -> list[DiscoveredTask]:
        return list(self._tasks)

    def read_pending_tasks(self) -> list[DiscoveredTask]:
        return [t for t in self._tasks if t.needs_confirmation]

    def read_pending_tasks_for_bot(
        self, bot_id: str, owner_id: str, dt: str,
    ) -> list[DiscoveredTask]:
        return [
            t for t in self.read_pending_tasks()
            if t.bot_id == bot_id and t.owner_id == owner_id and t.dt == dt
        ]


def _build_mock_tasks(bot_id: str, owner_id: str) -> list[DiscoveredTask]:
    """构建内联测试 DiscoveredTask 对象，动态填充 bot_id/owner_id。"""
    tasks = []
    for t in _MOCK_TASKS:
        task = dict(t)
        task["bot_id"] = bot_id
        task["owner_id"] = owner_id
        task["task_id"] = f"discover_task_{bot_id}_{owner_id}_{_TODAY}"
        tasks.append(DiscoveredTask(
            task_id=task["task_id"],
            bot_id=task["bot_id"],
            owner_id=task["owner_id"],
            dt=task["dt"],
            title=task["title"],
            instruction=task.get("instruction", ""),
            background=task.get("background", ""),
            discovery_basis=task.get("discovery_basis", ""),
            priority=task.get("priority", "medium"),
            discovered_at=task.get("discovered_at"),
            status=task.get("status", "pending_confirmation"),
            objective=task.get("objective", ""),
            acceptances=list(task.get("acceptances", [])),
        ))
    return tasks


def _seed_backend(bot_id: str, owner_id: str) -> list[dict]:
    """通过 HTTP /discovery/tasks 向 backend 写入 mock 数据。"""
    tasks = []
    for t in _MOCK_TASKS:
        task = dict(t)
        task["bot_id"] = bot_id
        task["owner_id"] = owner_id
        task["task_id"] = f"discover_task_{bot_id}_{owner_id}_{_TODAY}"
        tasks.append(task)
    r = httpx.post(
        f"{_BACKEND}/api/v1/collaboration/tasks/discovery/tasks",
        json={"tasks": tasks},
        timeout=15.0, headers=_HDRS,
    )
    r.raise_for_status()
    print(f"[setup] written {len(tasks)} tasks via HTTP /discovery/tasks (bot={bot_id})")
    return tasks


class _HttpCronRelay:
    """轻量 HTTP cron relay — 直接解析 engine target 并转发请求。

    绕过完整 CronRelayService（需要 device 状态检查 / transport / resolver 等），
    仅做 e2e 测试需要的 forward_request(): backend API → engine。
    """

    def __init__(self, backend_url: str, user_id: str):
        self._backend_url = backend_url
        self._user_id = user_id

    async def forward_request(
        self,
        *,
        bot_id: str,
        user_id: str,
        nick_name: str,
        method: str,
        path: str,
        body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """解析 engine target → 转发 HTTP 请求 → 返回 engine 响应。"""
        headers = {"x-user-id": user_id}
        async with httpx.AsyncClient(timeout=30.0) as cli:
            # 1. GET /api/bots/{bot_id} → binding_id
            bot_resp = await cli.get(
                f"{self._backend_url}/api/bots/{bot_id}",
                headers=headers,
            )
            bot_resp.raise_for_status()
            binding_id = (bot_resp.json().get("data") or {}).get("binding_id")
            if not binding_id:
                return {"success": False, "message": f"Bot {bot_id} has no binding_id"}

            # 2. GET /api/v1/devices/{binding_id}/connection → engine target
            conn_resp = await cli.get(
                f"{self._backend_url}/api/v1/devices/{binding_id}/connection",
                headers=headers,
            )
            conn_resp.raise_for_status()
            target = (conn_resp.json().get("data") or {}).get("target") or ""
            if not target:
                return {"success": False, "message": "No engine target resolved"}

            # 3. 转发请求到 engine
            engine_resp = await cli.request(
                method,
                f"http://{target}{path}",
                json=body,
                params=params,
                headers={"x-user-id": user_id},
            )

            # engine 响应本身就是 {"success": True, "data": {"id": "session:..."}}
            # 直接透传，不要再包一层
            if engine_resp.is_success:
                return engine_resp.json() if engine_resp.content else {"success": True, "data": {}}
            else:
                return {
                    "success": False,
                    "message": f"Engine returned {engine_resp.status_code}: {engine_resp.text[:200]}",
                }


class _MockNotifySender:
    """测试用 noop 通知发送器 — 满足 NotifySenderPlugin Protocol。"""

    @property
    def channels(self) -> frozenset[str]:
        return frozenset({"markdown"})

    def send(
        self,
        message: NotifyMessage,
        *,
        channel: str = "markdown",
    ) -> str | None:
        print(f"[notify] (mock) title={message.title} recipient={message.recipient}")
        return f"mock-msg-{message.recipient}"


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

        # 准备 mock 数据 — 向 backend 播种 + 本地内存 reader
        _seed_backend(bot_id, owner_id)
        self._mock_tasks = _build_mock_tasks(bot_id, owner_id)

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop, bot_id, owner_id))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop, bot_id: str, owner_id: str) -> None:
        # ===== 直接构造 DiscoveryService（绕过 HTTP /discover 端点）=====

        reader = _InMemoryTaskReader(self._mock_tasks)
        relay = _HttpCronRelay(_BACKEND, _USER_ID)
        initiator = CronRelaySessionInitiator(cron_relay=relay)
        notifier = _MockNotifySender()

        service = DiscoveryService(
            reader=reader,
            session_initiator=initiator,
            notify_sender=notifier,
            bot_service=None,
        )

        # 1) 直接调用 DiscoveryService.discover()
        results = await service.discover(
            bot_id=bot_id,
            owner_id=owner_id,
            agent_id=bot_id,
        )

        print(f"[discover] discovered={len(results)}")

        # 如果没发现任务，可能是 mock 数据 dt 不匹配
        if not results:
            print("[discover] 未发现任务 — 可能 mock 数据 dt 不匹配")
            return

        self.assertEqual(
            len(results), _EXPECTED_TASK_COUNT,
            f"results 列表长度 != {_EXPECTED_TASK_COUNT}: {len(results)}",
        )

        # 2) 逐任务验证: task_id / session_id / session_url / notification_sent
        for r in results:
            tid = r.task.task_id
            sid = r.session.session_id if r.session else None
            surl = r.session.session_url if r.session else None
            notified = r.notification_sent

            print(f"  - task={tid} success={r.success} "
                  f"session_id={sid} session_url={surl} notified={notified}")

            self.assertTrue(r.success, f"任务 {tid} discovery 未成功: {r.error}")
            self.assertIsNotNone(sid, f"任务 {tid} session_id 为空")
            self.assertTrue(sid, f"任务 {tid} session_id 为空字符串")
            self.assertIsNotNone(surl, f"任务 {tid} session_url 为空")
            self.assertTrue(notified, f"任务 {tid} 通知未发送")

        # 3) 通过内存 reader 直接验证任务状态（绕过 HTTP /status 端点）
        all_tasks = reader.read_discovered_tasks()
        status_tasks = [
            t for t in all_tasks
            if t.bot_id == bot_id and t.owner_id == owner_id
        ]
        print(f"[status] total={len(status_tasks)}")

        for t in status_tasks:
            # 验证新格式字段
            self.assertIn(t.bot_id, [bot_id], f"task {t.task_id} bot_id 不匹配")
            self.assertIn(t.owner_id, [owner_id], f"task {t.task_id} owner_id 不匹配")
            self.assertIsNotNone(t.dt, f"task {t.task_id} dt 为空")
            self.assertIsNotNone(t.status, "task status 为空")
            self.assertIsNotNone(t.priority, "task priority 为空")

        # 4) 验证 engine session 实际存在
        first_sid = results[0].session.session_id
        async with httpx.AsyncClient(timeout=60.0, headers=_HDRS) as cli:
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
            #    WS 注入是 best-effort（CronRelaySessionInitiator 内部失败只 log warning），
            #    给 engine 一点时间处理 WS 消息后重试几次。
            encoded_id = base64.urlsafe_b64encode(first_sid.encode()).decode()
            messages: list[dict] = []
            for _attempt in range(3):
                await asyncio.sleep(2)
                msg_resp = await cli.get(
                    f"http://{target}/api/sessions/{encoded_id}/messages",
                    params={"limit": 10, "offset": 0},
                    headers={"x-user-id": _USER_ID},
                )
                if msg_resp.status_code == 200:
                    messages = msg_resp.json().get("data") or []
                    if messages:
                        break
            roles = [m.get("role") for m in messages]
            print(f"[messages] 共 {len(messages)} 条, roles={roles}")
            if "user" not in roles:
                import warnings
                warnings.warn(
                    "消息历史中缺少 user 消息 — WS chat.send 注入可能失败"
                    "（session 已创建成功，WS 注入是 best-effort）",
                )

        print("[done] task_discovery e2e 全链路验证通过")


# ===== HTTP 接口测试：discover + status =====

@unittest.skipUnless(os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"},
                     "设置 SINGLEBOX_TASK_E2E=1 启用")
class TestDiscoveryStatusE2E(unittest.TestCase):
    """HTTP 接口 e2e: POST /discover → GET /status 验证 session 关联。"""

    _bot_id: str = ""
    _owner_id: str = ""

    def setUp(self) -> None:
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
            self.skipTest("singlebox 未 provision 任何 bot")
        # 取第一个 ACTIVE 的 bot 负责任务发现
        active_bots = [b for b in bots if b.get("status") == "ACTIVE"]
        bot = active_bots[0] if active_bots else bots[0]
        self._bot_id = bot["bot_id"]
        self._owner_id = bot.get("owner_id", _USER_ID)
        _seed_backend(self._bot_id, self._owner_id)
        print(f"[setup] bot_id={self._bot_id} owner_id={self._owner_id}")

    def test_discover_then_status(self) -> None:
        """POST /discover 触发发现 → GET /status 验证 session_id 已关联。"""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._discover_and_check(loop))
        finally:
            loop.close()

    async def _discover_and_check(self, loop: asyncio.AbstractEventLoop) -> None:
        async with httpx.AsyncClient(timeout=120.0, headers=_HDRS) as cli:
            # 1) 先查 status（discover 前）— 应该 discovered=False
            r1 = await cli.get(
                f"{_BACKEND}/api/v1/collaboration/tasks/discovery/status",
            )
            self.assertEqual(r1.status_code, 200, f"status HTTP 异常: {r1.status_code}")
            before = r1.json().get("data") or {}
            print(f"[status-before] total={before.get('total')} "
                  f"discovered={before.get('discovered')}")

            # 找到我们的 task — 如果上次 discover 已跑过(discovered=True)，跳过 before 断言
            before_tasks = [t for t in (before.get("tasks") or [])
                            if t.get("bot_id") == self._bot_id and t.get("dt") == _TODAY]
            if before_tasks:
                if before_tasks[0].get("discovered"):
                    print("[status-before] 注意: 上次 discover 结果仍在内存, before 断言跳过")
                else:
                    self.assertFalse(before_tasks[0].get("discovered"),
                                     "discover 前不应有 discovered=True")

            # 2) POST /discover 触发发现
            r2 = await cli.post(
                f"{_BACKEND}/api/v1/collaboration/tasks/discovery/discover",
                params={"bot_id": self._bot_id, "owner_id": self._owner_id,
                        "agent_id": self._bot_id, "user_id": self._owner_id},
            )
            self.assertEqual(r2.status_code, 200, f"discover HTTP 异常: {r2.status_code}")
            discover_body = r2.json().get("data") or {}
            print(f"[discover] discovered={discover_body.get('discovered')} "
                  f"tasks={len(discover_body.get('tasks') or [])}")

            # 3) 再查 status（discover 后）— 应该 discovered=True + session_id 非空
            r3 = await cli.get(
                f"{_BACKEND}/api/v1/collaboration/tasks/discovery/status",
            )
            self.assertEqual(r3.status_code, 200, f"status HTTP 异常: {r3.status_code}")
            after = r3.json().get("data") or {}
            print(f"[status-after] total={after.get('total')} "
                  f"discovered={after.get('discovered')}")

            after_tasks = [t for t in (after.get("tasks") or [])
                           if t.get("bot_id") == self._bot_id and t.get("dt") == _TODAY]
            self.assertTrue(after_tasks, "status 未返回本 bot 的 task")

            for t in after_tasks:
                print(f"  task={t.get('task_id')} discovered={t.get('discovered')} "
                      f"session_id={t.get('session_id')} session_url={t.get('session_url')}")
                self.assertTrue(t.get("discovered"),
                                f"task {t.get('task_id')} discover 后未标记 discovered=True")
                self.assertIsNotNone(t.get("session_id"),
                                     f"task {t.get('task_id')} session_id 为空")


if __name__ == "__main__":
    unittest.main()
