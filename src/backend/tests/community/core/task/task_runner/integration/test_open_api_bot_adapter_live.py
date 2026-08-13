"""OpenApiBotAdapter.send_and_wait live 集成测试(打真实 BaaS Open API,不经 MockTransport)。

默认跳过:下方配置常量置空占位,手动填入真实 BaaS Open API 配置(API_KEY / API_KEY_PREFIX /
BASE_URL / COOKIE / REFERER / BOT_ID)后,skipif 条件满足才会真正执行(用 `-s` 看打印的 run dict)。
"""

import pytest

from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiBotAdapter,
)


# ===== live 配置(置空占位;手动填入真实 BaaS Open API 配置后启用本测试)=====
API_KEY = ""
API_KEY_PREFIX = ""
BASE_URL = ""
COOKIE = ""
REFERER = ""
BOT_ID = ""
MESSAGE = "hi"


_LIVE_ENABLED = bool(API_KEY and BASE_URL and BOT_ID)


class _LiveKey:
    """真实 ApiKeyProvider(读取上方配置常量;填入后生效)。"""

    api_key = API_KEY
    api_key_prefix = API_KEY_PREFIX
    base_url = BASE_URL
    cookie = COOKIE
    referer = REFERER


@pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason="填入 BaaS Open API 配置(API_KEY / BASE_URL / BOT_ID)后启用 live 测试",
)
def test_send_and_wait_live_returns_terminal_run():
    adapter = OpenApiBotAdapter(_LiveKey())  # 真实 httpx.AsyncClient(base_url),非 MockTransport
    run = adapter.send_and_wait(bot_id=BOT_ID, message=MESSAGE, timeout=180.0, poll_interval=2.0)
    # send_and_wait 仅在终态返回(超时抛 OpenApiTimeoutError);此处断言终态并打印回答。
    assert run["status"] in ("COMPLETED", "FAILED")
    print(run)  # 用 pytest -s 查看真实回答 / 错误详情
