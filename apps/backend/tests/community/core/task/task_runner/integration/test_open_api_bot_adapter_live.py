"""OpenApiBotAdapter live integration test against an explicitly configured endpoint.

The test is disabled by default. Configure it only through environment variables;
credentials and session state must never be committed to the repository.
"""

from __future__ import annotations

import os
import unittest

from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiBotAdapter,
)


API_KEY = os.getenv("TASK_OPENAPI_API_KEY", "")
# When omitted, the adapter falls back to the first eight API-key characters.
API_KEY_PREFIX = os.environ.get("TASK_OPENAPI_API_KEY_PREFIX", "")
BASE_URL = os.environ.get("TASK_OPENAPI_BASE_URL", "")
COOKIE = os.environ.get("TASK_OPENAPI_COOKIE", "")
REFERER = os.environ.get("TASK_OPENAPI_REFERER", "")
BOT_ID = os.environ.get("TASK_OPENAPI_BOT_ID", "")
MESSAGE = os.environ.get("TASK_OPENAPI_MESSAGE", "帮我写一首赞美成都的古诗")

_LIVE_ENABLED = bool(API_KEY and BASE_URL and BOT_ID)


class _LiveKey:
    """ApiKeyProvider backed exclusively by explicit live-test configuration."""

    api_key = API_KEY
    api_key_prefix = API_KEY_PREFIX
    base_url = BASE_URL
    cookie = COOKIE
    referer = REFERER


@unittest.skipUnless(
    _LIVE_ENABLED,
    "设置 TASK_OPENAPI_API_KEY/BASE_URL/BOT_ID 后启用 live 测试",
)
class TestSendAndWaitLive(unittest.TestCase):
    def test_returns_terminal_run(self) -> None:
        adapter = OpenApiBotAdapter(_LiveKey())
        run = adapter.send_and_wait(
            bot_id=BOT_ID,
            message=MESSAGE,
            timeout=180.0,
            poll_interval=5.0,
        )
        self.assertIn(run["status"], ("COMPLETED", "FAILED"))


if __name__ == "__main__":
    unittest.main()
