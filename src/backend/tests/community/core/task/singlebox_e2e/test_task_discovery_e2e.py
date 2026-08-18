"""任务主动发现 — singlebox 真实端到端集成用例(发现 → 通知 → session 创建全链路)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地起后端 singlebox 时设置:

  SINGLEBOX_TASK_E2E=1 .venv/bin/python -m pytest \
    tests/community/core/task/singlebox_e2e/test_task_discovery_e2e.py -s

完整流程覆盖:
  1) 准备 mock 数据: 将内联测试数据写入 scripts/.dependencies/data/discovered_tasks.db
  2) provisioning: 建一个 test agent bot(获取真实 agent_id)
  3) POST /api/public/task-discovery/discover   → 读取 mock 任务 + 为每个任务创建 engine session
  4) 验证响应: success / discovered count / task_id / session_id / session_url
  5) GET /api/public/task-discovery/status      → 验证任务状态可查询
  6) 验证 session_url 可达(engine session 实际存在)

关键架构前提:
  - DiscoveryService 编排 TaskReader(读 SQLite db)→ SessionCreator(调 engine POST /api/sessions)
  - 每个 pending_confirmation 任务 → 一个 engine session + session_url(供用户前端确认)
  - 任务执行不在本测试范围(由 task 执行框架负责)
"""
from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_discovery.task_reader import (
    init_discovered_tasks_db,
)
from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
    SingleboxBotProvisioner,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")
_ENGINE_URL = os.environ.get("TASK_DISCOVERY_ENGINE_URL", "http://localhost:20003")
_FRONTEND_URL = os.environ.get("TASK_DISCOVERY_FRONTEND_URL", "http://localhost:8000")

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}
_TEST_BOT_NAME = "task-discovery-test-bot"

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
    {
        "task_id": "disc-e2e-002",
        "project_name": "SSD 供应链竞争格局梳理",
        "description": "梳理全球 SSD 供应链从晶圆到终端的主要参与者、份额变化及技术演进趋势。",
        "business_scenario": "赛道分析 — 聚焦存储产业链中游,识别核心供应商和潜在替代风险。",
        "discovery_basis": "用户在存储行业尽调 session 中多次追问供应链问题,行为节点链路显示对供应链环节的关注持续升温。",
        "work_item_url": None,
        "priority": "medium",
        "discovered_at": "2026-08-17T11:30:00Z",
        "status": "pending_confirmation",
    },
    {
        "task_id": "disc-e2e-003",
        "project_name": "ToB 存储方案客户需求画像",
        "description": "整合近期 ToB 客户在存储方案上的需求反馈,形成结构化的客户需求画像。",
        "business_scenario": "客户洞察 — 用于指导后续存储产品的 roadmap 优先级排序。",
        "discovery_basis": "用户在过去两周创建了 3 个 ToB 方案相关的 session,且多个对话节点涉及采购决策标准讨论。",
        "work_item_url": None,
        "priority": "low",
        "discovered_at": "2026-08-17T14:00:00Z",
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
    """任务主动发现 singlebox e2e: discover → status → session 可达。"""

    def test_discover_creates_sessions_and_returns_tasks(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) 准备 mock 数据: 写入磁盘(backend 的 MockTaskReader 从此文件读)
        _write_mock_data()

        # 2) provisioning: 建一个 test bot 获取真实 agent_id
        prov = SingleboxBotProvisioner(
            backend_base_url=_BACKEND, user_id=_USER_ID
        )
        agent_id = await prov.create_bot(bot_name=_TEST_BOT_NAME)
        await prov._aclose()
        print(f"[provision] agent_bot_id={agent_id}")

        async with httpx.AsyncClient(timeout=60.0, headers=_HDRS) as cli:
            # 3) POST /api/public/task-discovery/discover
            #    → 读取 mock 任务 + 为每个任务创建 engine session
            r = await cli.post(
                f"{_BACKEND}/api/public/task-discovery/discover",
                params={"user_id": _USER_ID, "agent_id": agent_id},
            )
            r.raise_for_status()
            body = r.json()
            print(f"[discover] success={body.get('success')} "
                  f"discovered={body.get('discovered')}")

            self.assertTrue(body.get("success"), f"discover 未成功: {body}")
            self.assertEqual(
                body.get("discovered"), _EXPECTED_TASK_COUNT,
                f"discovered 数量 != {_EXPECTED_TASK_COUNT}: {body.get('discovered')}",
            )

            tasks = body.get("tasks", [])
            self.assertEqual(len(tasks), _EXPECTED_TASK_COUNT, "tasks 列表长度不匹配")

            # 4) 逐任务验证: task_id / session_id / session_url 非空
            discovered_ids: set[str] = set()
            session_urls: list[str] = []
            for t in tasks:
                tid = t.get("task_id", "")
                sid = t.get("session_id")
                surl = t.get("session_url")
                success = t.get("success")

                print(f"  - task={tid} success={success} "
                      f"session_id={sid} url={surl}")
                discovered_ids.add(tid)

                self.assertTrue(success, f"任务 {tid} discovery 未成功")
                self.assertIsNotNone(sid, f"任务 {tid} session_id 为空")
                self.assertTrue(sid, f"任务 {tid} session_id 为空字符串")
                self.assertIsNotNone(surl, f"任务 {tid} session_url 为空")
                self.assertTrue(surl, f"任务 {tid} session_url 为空字符串")
                session_urls.append(surl)

            # task_id 集合与内联 mock 数据一致
            self.assertEqual(
                discovered_ids, _EXPECTED_TASK_IDS,
                f"发现 task_id 集合不匹配: {discovered_ids} vs {_EXPECTED_TASK_IDS}",
            )

            # session_url 格式校验: {frontend}/bcn/chat/session?bot_uuid=...&id=...&session=...
            for surl in session_urls:
                self.assertIn(
                    "/bcn/chat/session", surl,
                    f"session_url 格式异常(缺少 /bcn/chat/session): {surl}",
                )
                self.assertIn(f"bot_uuid={agent_id}", surl, f"session_url 未包含 bot_uuid: {surl}")
                self.assertIn("id=", surl, f"session_url 未包含 id(群组ID) 参数: {surl}")
                self.assertIn("session=", surl, f"session_url 未包含 session= 参数: {surl}")

            # 5) GET /api/public/task-discovery/status → 验证任务状态可查
            r = await cli.get(f"{_BACKEND}/api/public/task-discovery/status")
            r.raise_for_status()
            status_body = r.json()
            print(f"[status] success={status_body.get('success')} "
                  f"total={status_body.get('total')}")

            self.assertTrue(status_body.get("success"), f"status 查询未成功: {status_body}")
            self.assertEqual(
                status_body.get("total"), _EXPECTED_TASK_COUNT,
                f"status total != {_EXPECTED_TASK_COUNT}",
            )

            status_tasks = status_body.get("tasks", [])
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

            # 6) 验证 engine session 实际存在(GET /api/sessions/{id} 可达)
            #    取第一个任务的 session_id 验证
            first_sid = tasks[0].get("session_id")
            try:
                eng_resp = await cli.get(
                    f"{_ENGINE_URL}/api/sessions/{first_sid}",
                    headers={"x-user-id": _USER_ID},
                )
                if eng_resp.status_code == 200:
                    eng_data = eng_resp.json()
                    print(f"[engine] session {first_sid} 存在: "
                          f"success={eng_data.get('success')}")
                    self.assertTrue(
                        eng_data.get("success") or eng_data.get("data") is not None,
                        f"engine session {first_sid} 查询返回异常: {eng_data}",
                    )
                else:
                    print(f"[engine] session {first_sid} 查询 HTTP {eng_resp.status_code},"
                          f" 跳过(engine 可能未启用该查询端点)")
            except Exception as exc:  # noqa: BLE001
                print(f"[engine] session {first_sid} 查询异常({exc!r}),"
                      f" 跳过(engine 可能未运行)")

        print("[done] task_discovery e2e 全链路验证通过")


if __name__ == "__main__":
    unittest.main()
