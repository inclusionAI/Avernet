"""SingleboxBcsAdapter live 集成测试(打真实本地 BCS :21000,建群 + 建 session,不经 Mock)。

默认跳过:需 ``SINGLEBOX_TASK_E2E=1`` 且提供 ``SINGLEBOX_BOT_ID``(一个已在本地 BCS 注册的 bot id,
即 singlebox 起栈后经 BCS WS onboarding 过的 bot)。本地起好 singlebox(``./scripts/singlebox.sh start all``)后:

  SINGLEBOX_TASK_E2E=1 SINGLEBOX_BOT_ID=<已注册的 bot id> SINGLEBOX_USER_ID=35983 \
    .venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/test_run_bcs_group.py -s

验证单 box 真实链路经 ``SingleboxBcsAdapter``(继承 BcsHttpAdapter,本地 require_authentication=false
→ HMAC 头被忽略)能:
  1) ``POST /groups`` 建群 → 返 ``group_id``(本地用 ``id``,adapter 已映射);
  2) ``POST /groups/{group_id}/sessions`` 建 session → 返 ``session_id``。

注:本地 BCS ``create_group`` 会校验 ``driver_bot``/participants 在 BCS 已注册,故 ``SINGLEBOX_BOT_ID``
必须是已 onboarding 的真实 bot,否则 404 ``bot_not_found``。
"""
from __future__ import annotations

import asyncio
import os
import unittest

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupRequest,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.singlebox_bcs_adapter import (
    SingleboxBcsAdapter,
)

_LIVE_ENABLED = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"} and bool(
    os.environ.get("SINGLEBOX_BOT_ID", "").strip()
)
# 本地 BCS 地址(默认 :21000);driver/参与者复用同一个已注册 bot,降低前置条件。
_BCS_URL = os.environ.get("SINGLEBOX_BCS_URL", "http://localhost:21000")

@unittest.skipUnless(_LIVE_ENABLED, "设置 SINGLEBOX_TASK_E2E=1 + SINGLEBOX_BOT_ID 后启用 live 测试")
class TestRunBcsGroupLive(unittest.TestCase):
    """singlebox 真实链路:经 SingleboxBcsAdapter 建群 + 建 session。"""

    def setUp(self) -> None:
        self._adapter = SingleboxBcsAdapter(
            LocalBcsTokenProvider(base_url=_BCS_URL),
        )
        self._loop = asyncio.new_event_loop()
        self._group_id: str | None = None

    def tearDown(self) -> None:
        try:
            # adapter 持 httpx client,无后台线程(SingleboxEngineAdapter 才有 bg loop);关 client 即可。
            self._loop.run_until_complete(self._adapter._client.aclose())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001  清理期不抛
            pass
        finally:
            self._loop.close()

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def test_create_group_and_session(self) -> None:
        """真实建群 → 建 session:两条能力都应返非空 id。"""
        # 1) 建群:driver + 一个参与者,均复用同一个已注册 bot(singlebox driver 兼当 participant)。
        result = self._run(self._adapter.create_group(BcsCreateGroupRequest(
            driver_bot='bot_6f544a9d',
            participants=[{"bot_uuid": 'bot_92c2f019', "role": "consultant"}],
            group_strategy="chat",
            context="歌词创作",
            topic="一起歌词创作",
        )))
        self.assertIsNotNone(result.group_id, "create_group 未返 group_id")
        self.assertTrue(result.group_id, "create_group 返空 group_id")
        self._group_id = result.group_id
        print(f"[create_group] group_id={result.group_id} session_id={result.session_id}")

        # 2) 建 session 在该群上。
        session_id = self._run(self._adapter.create_session(
            self._group_id, bootstrap_prompt="每个人都创作一首赞美成都的歌词",
        ))
        self.assertIsNotNone(session_id, "create_session 未返 session_id")
        self.assertTrue(session_id, "create_session 返空 session_id")
        print(f"[create_session] group_id={self._group_id} session_id={session_id}")


if __name__ == "__main__":
    unittest.main(verbosity=2)