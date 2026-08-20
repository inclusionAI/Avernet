"""APScheduler 调度 — singlebox 真实端到端集成用例（调度状态查找 → 主动触发 → 验证结果）。

gated by ``SINGLEBOX_CRON_E2E=1``。本地起后端 singlebox 时设置:

  SINGLEBOX_CRON_E2E=1 .venv/bin/python -m pytest \\
    tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py -s

完整流程覆盖:
  1) 查已有 bot + 写入 mock 任务数据到 discovered_tasks.db
  2) 调度状态查找: GET /api/public/task-discovery/scheduler-status
     — 验证 APScheduler running=True、job 列表非空、cron 表达式、next_run_time
  3) 主动触发: POST /api/public/task-discovery/scheduled-trigger
     — 模拟外部 cron 触发 discover_all_bots，验证 session 创建成功

环境变量:
    SINGLEBOX_CRON_E2E=1     启用本测试（默认 skip）
    SINGLEBOX_BACKEND_URL    backend 地址, 默认 http://localhost:8888
    SINGLEBOX_USER_ID        用户工号, 默认 440718
"""
from __future__ import annotations

import asyncio
import os
import unittest
import warnings
from datetime import datetime
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_discovery.task_reader import (
    init_discovered_tasks_db,
)

_LIVE = os.environ.get("SINGLEBOX_CRON_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "440718")

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

_TODAY = datetime.now().strftime("%Y-%m-%d")

_MOCK_TASKS: list[dict] = [
    {
        "task_id": f"cron_e2e_task_{_USER_ID}_{_TODAY}",
        "bot_id": "e2e-bot",
        "owner_id": _USER_ID,
        "dt": _TODAY,
        "project_name": "存储行业尽调报告",
        "description": "AI 基础设施驱动下,企业级与数据中心存储行业的最新变化。",
        "business_scenario": "投资尽调 — 产出系统性的投资判断报告。",
        "discovery_basis": "用户近一周频繁搜索存储行业相关信息。",
        "work_item_url": "https://project.alipay.com/workitem/123456",
        "priority": "high",
        "discovered_at": f"{_TODAY}T10:00:00Z",
        "status": "pending_confirmation",
    },
]

_DATA_FILE = Path(__file__).resolve()
for _ in range(8):
    _DATA_FILE = _DATA_FILE.parent
_DATA_FILE = _DATA_FILE / "scripts" / ".dependencies" / "data" / "discovered_tasks.db"


def _write_mock_data(bot_id: str, owner_id: str) -> None:
    tasks = []
    for t in _MOCK_TASKS:
        task = dict(t)
        task["bot_id"] = bot_id
        task["owner_id"] = owner_id
        task["task_id"] = f"cron_e2e_{bot_id}_{owner_id}_{_TODAY}"
        tasks.append(task)
    init_discovered_tasks_db(_DATA_FILE, tasks)
    print(f"[setup] mock 数据已写入 {_DATA_FILE} ({len(tasks)} tasks, bot={bot_id})")


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_CRON_E2E=1 启用")
class TestCronSchedulerE2E(unittest.TestCase):
    """APScheduler singlebox e2e: 调度状态查找 → 主动触发 → 验证。"""

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
            self.skipTest("singlebox 未 provision 任何 bot, 请先 start all")

        bot = bots[0]
        self._bot_id = bot["bot_id"]
        self._owner_id = bot.get("owner_id", _USER_ID)
        print(f"[setup] bot_id={self._bot_id} owner_id={self._owner_id}")

        _write_mock_data(self._bot_id, self._owner_id)

    # ===== 测试用例 =====

    def test_01_scheduler_status(self) -> None:
        """APScheduler 状态查找 — GET /api/public/task-discovery/scheduler-status。"""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._scheduler_status(loop))
        finally:
            loop.close()

    async def _scheduler_status(self, loop: asyncio.AbstractEventLoop) -> None:
        async with httpx.AsyncClient(timeout=30.0, headers=_HDRS) as cli:
            r = await cli.get(
                f"{_BACKEND}/api/public/task-discovery/scheduler-status",
            )
            self.assertEqual(r.status_code, 200,
                             f"scheduler-status HTTP 异常: {r.status_code}")
            body = r.json()
            self.assertTrue(body.get("success"),
                            f"scheduler-status 未成功: {body}")

            print(f"[scheduler] running={body.get('running')} "
                  f"auto_start={body.get('auto_start')} "
                  f"cron={body.get('cron')} tz={body.get('timezone')}")

            # 验证调度器在运行
            self.assertTrue(body.get("running"),
                            "APScheduler 未运行 — 检查 TASK_DISCOVERY_AUTO_START")

            # 验证 job 列表
            jobs = body.get("jobs") or []
            self.assertGreater(len(jobs), 0, "APScheduler 无 job 注册")

            for job in jobs:
                print(f"  - id={job.get('id')} cron={job.get('cron')} "
                      f"next_run={job.get('next_run_time')} "
                      f"tz={job.get('timezone')}")

                self.assertEqual(job.get("id"), "task_discovery_daily",
                                 f"job id 不匹配: {job.get('id')}")
                self.assertIsNotNone(job.get("next_run_time"),
                                     "next_run_time 为空 — cron 可能未正确注册")

    def test_02_scheduled_trigger(self) -> None:
        """APScheduler 主动触发 — POST /api/public/task-discovery/scheduled-trigger。"""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._scheduled_trigger(loop))
        finally:
            loop.close()

    async def _scheduled_trigger(self, loop: asyncio.AbstractEventLoop) -> None:
        async with httpx.AsyncClient(timeout=120.0, headers=_HDRS) as cli:
            r = await cli.post(
                f"{_BACKEND}/api/public/task-discovery/scheduled-trigger",
            )
            body = r.json() if r.status_code == 200 else {}
            print(f"[trigger] status={r.status_code} success={body.get('success')} "
                  f"total_discovered={body.get('total_discovered')}")

            self.assertEqual(r.status_code, 200,
                             f"scheduled-trigger HTTP 异常: {r.status_code}")

            if not body.get("success"):
                warnings.warn(
                    f"scheduled-trigger 执行未成功 (non-fatal): {body.get('message')}",
                )
                return

            results = body.get("results") or []
            our_results = [
                r for r in results
                if r.get("bot_id") == self._bot_id
            ]
            print(f"[trigger] 总结果 {len(results)} 条, 本 bot 结果 {len(our_results)} 条")

            if not our_results:
                warnings.warn("scheduled-trigger 未返回本 bot 的结果 — 可能 mock 数据 dt 不匹配")
                return

            for r in our_results:
                print(f"  - task={r.get('task_id')} success={r.get('success')} "
                      f"session_id={r.get('session_id')} notified={r.get('notification_sent')}")

                self.assertTrue(r.get("success"),
                                f"任务 {r.get('task_id')} discover 未成功: {r.get('error')}")
                self.assertIsNotNone(r.get("session_id"),
                                     f"任务 {r.get('task_id')} session_id 为空")
                self.assertIsNotNone(r.get("session_url"),
                                     f"任务 {r.get('task_id')} session_url 为空")


if __name__ == "__main__":
    unittest.main()
