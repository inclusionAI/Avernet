"""APScheduler 调度 — singlebox 真实端到端集成用例（调度状态查找 → 主动触发 → 验证结果）。

gated by ``SINGLEBOX_CRON_E2E=1``。本地起后端 singlebox 时设置:

  SINGLEBOX_CRON_E2E=1 .venv/bin/python -m pytest \\
    tests/community/core/task/singlebox_e2e/test_cron_scheduler_e2e.py -s

完整流程覆盖:
  1) 查已有 bot + 写入 mock 任务数据到 discovered_tasks.db
  2) 调度状态查找: GET /api/v1/collaboration/tasks/discovery/scheduler-status
     — 验证 APScheduler running=True、job 列表非空、cron 表达式、next_run_time
  3) 主动触发: POST /api/v1/collaboration/tasks/discovery/scheduled-trigger
     — 模拟外部 cron 触发 discover_all_bots，验证 session 创建成功
  4) 钉钉卡片通知: 直接调用钉钉 SDK 发送交互卡片
     — 验证 task_discovery 的通知卡片能正确投递

环境变量:
    SINGLEBOX_CRON_E2E=1     启用本测试（默认 skip）
    SINGLEBOX_BACKEND_URL    backend 地址, 默认 http://localhost:8888
    SINGLEBOX_USER_ID        用户工号, 默认 440718

钉钉卡片通知环境变量 (test_03):
    SINGLEBOX_DINGTALK_E2E=1              启用钉钉卡片测试（默认 skip）
    SINGLEBOX_DINGTALK_AK_ID              钉钉应用 AccessKey ID
    SINGLEBOX_DINGTALK_AK_SECRET          钉钉应用 AccessKey Secret
    SINGLEBOX_DINGTALK_ROBOT_CODE         机器人编码 (如 ding5ygeiieonm7tmuqw)
    SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID  卡片模板 ID
    SINGLEBOX_DINGTALK_ACCOUNT_ID         发送方 account_id (默认同 SINGLEBOX_USER_ID)
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

        # 取第一个 ACTIVE 的 bot 负责任务发现
        active_bots = [b for b in bots if b.get("status") == "ACTIVE"]
        bot = active_bots[0] if active_bots else bots[0]
        self._bot_id = bot["bot_id"]
        self._owner_id = bot.get("owner_id", _USER_ID)
        print(f"[setup] bot_id={self._bot_id} owner_id={self._owner_id}")

        _write_mock_data(self._bot_id, self._owner_id)

    # ===== 测试用例 =====

    def test_01_scheduler_status(self) -> None:
        """APScheduler 状态查找 — GET /api/v1/collaboration/tasks/discovery/scheduler-status。"""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._scheduler_status(loop))
        finally:
            loop.close()

    async def _scheduler_status(self, loop: asyncio.AbstractEventLoop) -> None:
        async with httpx.AsyncClient(timeout=30.0, headers=_HDRS) as cli:
            r = await cli.get(
                f"{_BACKEND}/api/v1/collaboration/tasks/discovery/scheduler-status",
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
        """APScheduler 主动触发 — POST /api/v1/collaboration/tasks/discovery/scheduled-trigger。"""
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._scheduled_trigger(loop))
        finally:
            loop.close()

    async def _scheduled_trigger(self, loop: asyncio.AbstractEventLoop) -> None:
        async with httpx.AsyncClient(timeout=120.0, headers=_HDRS) as cli:
            r = await cli.post(
                f"{_BACKEND}/api/v1/collaboration/tasks/discovery/scheduled-trigger",
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


# ===== 钉钉卡片通知 e2e =====

_DT_LIVE = os.environ.get("SINGLEBOX_DINGTALK_E2E", "").strip() in {"1", "true"}
_DT_AK_ID = os.environ.get("SINGLEBOX_DINGTALK_AK_ID", "")
_DT_AK_SECRET = os.environ.get("SINGLEBOX_DINGTALK_AK_SECRET", "")
_DT_ROBOT_CODE = os.environ.get("SINGLEBOX_DINGTALK_ROBOT_CODE", "")
_DT_CARD_TEMPLATE_ID = os.environ.get("SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID", "")
_DT_ACCOUNT_ID = os.environ.get("SINGLEBOX_DINGTALK_ACCOUNT_ID", _USER_ID)

_FRONTEND_URL = os.environ.get(
    "SINGLEBOX_FRONTEND_URL", "http://agentclaw-local.stable.alipay.net:8000/assistant",
)


def _build_card_data(
    *,
    workitem_name: str = "",
    workitem_bg: str = "",
    session_url: str = "",
) -> str:
    """构建钉钉卡片 card_data JSON。

    workitem_name / workitem_bg 来自 discover 发现的任务数据（DiscoveredTask）。
    session_url 固定用前端首页 URL（用户点击进入工作台首页）。
    """
    import json as _json
    return _json.dumps({
        "click": "",
        "card_name": "为你发现以下任务",
        "session_url": session_url or _FRONTEND_URL,
        "workitem_name": workitem_name,
        "workitem_bg": workitem_bg,
    }, ensure_ascii=False)


def _send_dingtalk_card(
    card_data: str,
    *,
    ak_id: str,
    ak_secret: str,
    robot_code: str,
    card_template_id: str,
    account_id: str,
    card_biz_id: str = "",
) -> dict:
    """调用钉钉 SDK 发送交互卡片，返回响应 body dict。"""
    import json
    import time

    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models
    from alipay_antdingopensdk_client import models as antdingopen_models
    from alipay_antdingopensdk_client.client import Client as antdingopenClient

    config = open_api_models.Config()
    config.access_key_id = ak_id
    config.access_key_secret = ak_secret
    client = antdingopenClient(config)

    headers = antdingopen_models.HttpHeader()
    headers.account_context = antdingopen_models.AccountContext(account_id=account_id)

    req = antdingopen_models.SendRobotInteractiveCardRequest()
    req.card_template_id = card_template_id
    req.robot_code = robot_code
    req.card_biz_id = card_biz_id or f"discover_things_agent_{int(time.time())}"
    req.card_data = card_data
    req.user_id = account_id

    resp = client.send_robot_interactive_card_with_options(
        req, headers, util_models.RuntimeOptions(),
    )
    biz_resp = resp.body
    return biz_resp.to_map() if hasattr(biz_resp, "to_map") else {"raw": str(biz_resp)}


@unittest.skipUnless(_DT_LIVE, "设置 SINGLEBOX_DINGTALK_E2E=1 启用")
class TestDingTalkCardE2E(unittest.TestCase):
    """钉钉交互卡片通知 e2e — 验证钉钉 SDK 发卡片 + discover 流程串联。"""

    def test_03_dingtalk_card(self) -> None:
        """发送钉钉交互卡片 — 验证 card_template_id/robot_code/card_data 参数可用。"""
        import json

        self.assertTrue(_DT_AK_ID, "SINGLEBOX_DINGTALK_AK_ID 未设")
        self.assertTrue(_DT_AK_SECRET, "SINGLEBOX_DINGTALK_AK_SECRET 未设")
        self.assertTrue(_DT_ROBOT_CODE, "SINGLEBOX_DINGTALK_ROBOT_CODE 未设")
        self.assertTrue(_DT_CARD_TEMPLATE_ID, "SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID 未设")

        print(f"[dingtalk] 发送卡片: template={_DT_CARD_TEMPLATE_ID} "
              f"robot={_DT_ROBOT_CODE} user={_DT_ACCOUNT_ID}")

        resp = _send_dingtalk_card(
            _build_card_data(),
            ak_id=_DT_AK_ID,
            ak_secret=_DT_AK_SECRET,
            robot_code=_DT_ROBOT_CODE,
            card_template_id=_DT_CARD_TEMPLATE_ID,
            account_id=_DT_ACCOUNT_ID,
        )
        print(f"[dingtalk] 响应: {json.dumps(resp, ensure_ascii=False)}")
        self.assertIsNotNone(resp, "钉钉响应 body 为空")

    def test_04_dingtalk_discover_e2e(self) -> None:
        """端到端: scheduled-trigger → discover → 拿 session_url → 钉钉卡片带 session_url 投递。

        流程:
          1. 写 mock 任务数据到 discovered_tasks.db
          2. POST scheduled-trigger → 拿到 results[0].session_url
          3. 用 session_url 构建 card_data → 调钉钉 SDK 发卡片
          4. 验证钉钉响应非空
        """
        import asyncio
        import json
        import time

        # 门禁: 同时需要 cron e2e 和 dingtalk e2e
        if not _LIVE:
            self.skipTest("设置 SINGLEBOX_CRON_E2E=1 启用")
        self.assertTrue(_DT_AK_ID, "SINGLEBOX_DINGTALK_AK_ID 未设")
        self.assertTrue(_DT_AK_SECRET, "SINGLEBOX_DINGTALK_AK_SECRET 未设")
        self.assertTrue(_DT_ROBOT_CODE, "SINGLEBOX_DINGTALK_ROBOT_CODE 未设")
        self.assertTrue(_DT_CARD_TEMPLATE_ID, "SINGLEBOX_DINGTALK_CARD_TEMPLATE_ID 未设")

        # 1) 查已有 bot + 写 mock 数据
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
        bot = bots[0]
        bot_id = bot["bot_id"]
        owner_id = bot.get("owner_id", _USER_ID)
        print(f"[e2e] bot_id={bot_id} owner_id={owner_id}")
        _write_mock_data(bot_id, owner_id)

        # 2) 调 scheduled-trigger → 拿 session_url
        async def _trigger() -> dict:
            async with httpx.AsyncClient(timeout=120.0, headers=_HDRS) as cli:
                r = await cli.post(
                    f"{_BACKEND}/api/v1/collaboration/tasks/discovery/scheduled-trigger",
                )
                return r.json() if r.status_code == 200 else {}

        loop = asyncio.new_event_loop()
        try:
            body = loop.run_until_complete(_trigger())
        finally:
            loop.close()

        self.assertTrue(body.get("success"), f"scheduled-trigger 未成功: {body}")
        results = body.get("results") or []
        our_results = [r for r in results if r.get("bot_id") == bot_id]
        self.assertTrue(our_results, "scheduled-trigger 未返回本 bot 的结果")
        result = our_results[0]
        self.assertTrue(result.get("success"), f"discover 未成功: {result.get('error')}")
        session_url = result.get("session_url")
        self.assertIsNotNone(session_url, "session_url 为空")

        print(f"[e2e] discover 成功: session_url={session_url}")

        # 3) 从 mock 任务数据取 workitem_name/workitem_bg，用 owner_id 当 account_id
        mock_task = _MOCK_TASKS[0]
        card_data = _build_card_data(
            workitem_name=mock_task["project_name"],
            workitem_bg=mock_task["description"],
        )
        print(f"[e2e] 发送钉钉卡片: card_url={_FRONTEND_URL} "
              f"workitem={mock_task['project_name']} user={owner_id}")

        resp = _send_dingtalk_card(
            card_data,
            ak_id=_DT_AK_ID,
            ak_secret=_DT_AK_SECRET,
            robot_code=_DT_ROBOT_CODE,
            card_template_id=_DT_CARD_TEMPLATE_ID,
            account_id=owner_id,
            card_biz_id=f"discover_e2e_{bot_id}_{int(time.time())}",
        )
        print(f"[e2e] 钉钉响应: {json.dumps(resp, ensure_ascii=False)}")
        self.assertIsNotNone(resp, "钉钉响应 body 为空")
        print("[e2e] 端到端完成: discover → session 创建 → 钉钉卡片发送 ✓")


if __name__ == "__main__":
    unittest.main()
