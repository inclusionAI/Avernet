"""SingleboxEngineAdapter live 集成测试(打真实 singlebox per-bot 引擎 WebSocket,不经 Mock)。

默认跳过:需 ``SINGLEBOX_TASK_E2E=1`` 且提供 ``SINGLEBOX_BOT_ID``(<bot_id>:建立于 singlebox 产品界面或
``POST /api/bots``)。本地起好 singlebox(``./scripts/singlebox.sh start all``)后:

  SINGLEBOX_TASK_E2E=1 SINGLEBOX_BOT_ID=20260814_yfchg86x \
    .venv/bin/python -m pytest tests/community/core/task/task_runner/integration/test_singlebox_engine_adapter_live.py -s

验证两条 OpenApiBotPort 路径均返 BaaS-shaped 终态 run dict ``{status, result{content}}``,供下游
``SingleBotRunTranslator`` / ``_parse_children`` / ``_parse_search_result`` 复用(契约一致)。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest

from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    SingleboxEngineAdapter,
)

_LIVE_ENABLED = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"} and bool(
    os.environ.get("SINGLEBOX_BOT_ID", "").strip()
)
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "146836")
_BOT_ID = os.environ.get("SINGLEBOX_BOT_ID", "")


@unittest.skipUnless(_LIVE_ENABLED, "设置 SINGLEBOX_TASK_E2E=1 + SINGLEBOX_BOT_ID 后启用 live 测试")
class TestSingleboxEngineAdapterLive(unittest.TestCase):
    def setUp(self) -> None:
        self._adapter = SingleboxEngineAdapter(backend_base_url=_BACKEND, user_id=_USER_ID)
        self._loop = asyncio.new_event_loop()

    def tearDown(self) -> None:
        try:
            self._loop.run_until_complete(self._adapter._aclose())
        finally:
            self._loop.close()

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def test_send_and_wait_async_returns_completed_run(self) -> None:
        """plan/dispatch 路径:send_and_wait_async 同步收 chat final → COMPLETED + content。"""
        run = self._run(
            self._adapter.send_and_wait_async(
                bot_id=_BOT_ID, message="你好，请用一句话确认收到这条任务消息。", timeout=60.0
            )
        )
        self.assertIn(run["status"], ("COMPLETED", "FAILED"))
        print("[send_and_wait_async]", run)
        self.assertEqual(run["status"], "COMPLETED")
        self.assertTrue(run.get("result", {}).get("content"))

    def test_send_message_then_poll_get_run(self) -> None:
        """executor/poller 路径:send_message 立即返 run_id,轮询 get_run 到终态。"""
        sent = self._run(
            self._adapter.send_message(
                bot_id=_BOT_ID, message="再回复一句：已就绪。", metadata={"phase": "execute"}
            )
        )
        rid = sent.run_id
        self.assertTrue(rid.startswith("ws_"))
        deadline = time.time() + 60
        run: dict = {}
        while time.time() < deadline:
            run = self._run(self._adapter.get_run(rid))
            if str(run.get("status", "")).upper() in ("COMPLETED", "FAILED"):
                break
            time.sleep(1)
        print("[get_run terminal]", run)
        self.assertEqual(run["status"], "COMPLETED")
        self.assertTrue(run.get("result", {}).get("content"))

    def test_ensure_grant_caches_target(self) -> None:
        """ensure_grant 解析并缓存 bot → 引擎 target,不抛。"""
        self._run(self._adapter.ensure_grant(_BOT_ID))
        self.assertIn(_BOT_ID, self._adapter._targets)
        self.assertTrue(self._adapter._targets[_BOT_ID].startswith("localhost:"))
