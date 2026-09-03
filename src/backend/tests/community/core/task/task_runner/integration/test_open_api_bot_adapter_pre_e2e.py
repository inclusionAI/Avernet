"""OpenApiBotAdapter 预发环境 e2e(unittest;打真实预发 BaaS Open API,不经 MockTransport)。

默认跳过:填入预发 BaaS Open API 配置(``AVERNET_PRE_OPENAPI_API_KEY`` + ``AVERNET_PRE_OPENAPI_BOT_ID``;
可选 ``AVERNET_PRE_OPENAPI_COOKIE`` / ``AVERNET_PRE_OPENAPI_PREFIX``)后,skipUnless 条件满足才真正执行
(unittest 默认不缓冲 stdout,可直接看打印的 run dict)。

跑法(从仓库根):

  AVERNET_PRE_OPENAPI_API_KEY=<预发api_key> \\
  AVERNET_PRE_OPENAPI_BOT_ID=<预置bot_id> \\
  [AVERNET_PRE_OPENAPI_BASE_URL=https://agentclaw-pre.alipay.com] \\
  [AVERNET_PRE_OPENAPI_COOKIE=<cookie文件路径 或 登录cookie原文(用于 grant)>] \\
  [AVERNET_PRE_OPENAPI_PREFIX=<真实路径前缀(留空走 adapter 默认 api_key[:8])>] \\
  [AVERNET_PRE_OPENAPI_MESSAGE=<prompt>] \\
  [AVERNET_PRE_OPENAPI_TIMEOUT=180] \\
  [AVERNET_PRE_OPENAPI_POLL_INTERVAL=5] \\
  src/backend/.venv/bin/python -m pytest \\
    src/backend/tests/community/core/task/task_runner/integration/test_open_api_bot_adapter_pre_e2e.py -s

# 场景

OpenApiBotAdapter 直连预发 BaaS Open API(Bearer api_key,不经本后端 / 不走 cookie 网关):
ensure_grant(GET allowed-bots;缺则 POST grant,用登录 Cookie+Referer 非 Bearer)→
send_message(POST /openapi/v1/messages,Bearer,拿 message_id=run_id)→ 轮询 get_run 到终态(COMPLETED/FAILED)。
用例只做:``send_and_wait`` 一次拿回终态并校验 + 打印,与现有 ``test_open_api_bot_adapter_live`` 同手法,
但默认 host = 预发(``agentclaw-pre.alipay.com``)。

# 预发接入默认

- BASE_URL 默认预发 host ``https://agentclaw-pre.alipay.com``(对齐 corp overlay ``openapi_bot.base_url_pre``),
  填了 ``AVERNET_PRE_OPENAPI_BASE_URL`` 则覆盖。
- api_key_prefix 留空 → adapter 回落 ``api_key[:_DEFAULT_KEY_PREFIX_LEN]``(本仓库 = 8);
  预发 key ``xQNGQIaa...`` 前 8 位即真实路径段,故默认即可对齐。若某 key 真实 prefix ≠ 前 8 位,显式传
  ``AVERNET_PRE_OPENAPI_PREFIX``。
- cookie 仅在 grant 时需要(bot 未在 ``allowed_bots`` → POST grant 需登录态,非 Bearer);
  预发若已 OOB 预授权该 bot 则留空。``AVERNET_PRE_OPENAPI_COOKIE`` 若指向一个已存在文件,则从该文件读 cookie
  内容(单行,去末尾换行);否则把值当 cookie 原文。grant 403 → OpenApiAuthError 透传,用例失败并暴露原因。
"""
# 1. 临时从 Python 搜索路径中移除导致冲突的 plugins/forwarder 路径(假 httpx 抢占真 httpx)
import sys
sys.path = [p for p in sys.path if 'plugins/forwarder' not in p.replace('\\', '/')]

# 2. 如果之前已经错误地导入了本地假 httpx(没有 AsyncClient),从缓存清除
if 'httpx' in sys.modules:
    if not hasattr(sys.modules['httpx'], 'AsyncClient'):
        del sys.modules['httpx']

# 3. 强制导入官方真正的 httpx 并注入到系统缓存
import httpx  # noqa: E402

import os
import unittest

from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (  # noqa: E402
    OpenApiBotAdapter,
)


# ===== 预发 live 配置(API_KEY / BOT_ID 必填;其余可环境变量覆写;外面未设则用此处默认)=====
_API_KEY = os.environ.get("AVERNET_PRE_OPENAPI_API_KEY", "").strip()
_BOT_ID = os.environ.get("AVERNET_PRE_OPENAPI_BOT_ID", "").strip()
_BASE_URL = os.environ.get(
    "AVERNET_PRE_OPENAPI_BASE_URL", "https://agentclaw-pre.alipay.com",
).strip().rstrip("/")
_PREFIX = os.environ.get("AVERNET_PRE_OPENAPI_PREFIX", "").strip()
_COOKIE_IN = os.environ.get("AVERNET_PRE_OPENAPI_COOKIE", "").strip()
# 支持两种:指向已存在文件 → 从该文件读(单行,去末尾换行);否则当 cookie 原文。读失败兜底为空,不让 cookie 文件问题破坏收集。
if _COOKIE_IN and os.path.isfile(_COOKIE_IN):
    try:
        with open(_COOKIE_IN, "r", encoding="utf-8") as _f:
            _COOKIE = _f.read().strip()
        _COOKIE_SRC = "file"
    except OSError:
        _COOKIE = ""
        _COOKIE_SRC = "file-read-error"
elif _COOKIE_IN:
    _COOKIE = _COOKIE_IN
    _COOKIE_SRC = "inline"
else:
    _COOKIE = ""
    _COOKIE_SRC = "none"
_MESSAGE = os.environ.get(
    "AVERNET_PRE_OPENAPI_MESSAGE",
    "这是一条 OpenApiBotAdapter 预发 e2e 探测消息,请确认收到。",
).strip()
_TIMEOUT = float(os.environ.get("AVERNET_PRE_OPENAPI_TIMEOUT", "180"))
_POLL_INTERVAL = float(os.environ.get("AVERNET_PRE_OPENAPI_POLL_INTERVAL", "5"))

# presence-gating(与现有 test_open_api_bot_adapter_live 同款):仅 API_KEY + BOT_ID 即启用
_LIVE = bool(_API_KEY and _BOT_ID)


class _PreKey:
    """真实 OpenApiBotAdapter 预发 e2e 的 ApiKeyProvider(读上方配置;填入后生效)。"""

    api_key = _API_KEY
    api_key_prefix = _PREFIX
    base_url = _BASE_URL
    cookie = _COOKIE
    referer = ""


@unittest.skipUnless(
    _LIVE,
    "填入预发 BaaS Open API 配置(AVERNET_PRE_OPENAPI_API_KEY + AVERNET_PRE_OPENAPI_BOT_ID)后启用 live 测试",
)
class TestOpenApiBotAdapterPreE2E(unittest.TestCase):
    def test_send_and_wait_returns_terminal(self) -> None:
        adapter = OpenApiBotAdapter(_PreKey())  # 真实 httpx.AsyncClient(base_url),非 MockTransport
        run = adapter.send_and_wait(
            bot_id=_BOT_ID, message=_MESSAGE, timeout=_TIMEOUT, poll_interval=_POLL_INTERVAL,
        )
        # send_and_wait 仅在终态返回(超时抛 OpenApiTimeoutError);此处断言终态并打印。
        self.assertIn(run["status"], ("COMPLETED", "FAILED"))
        print(run)  # 看真实回答 / 错误详情
        print(f"[pre-e2e] base_url={_BASE_URL} bot_id={_BOT_ID} "
              f"prefix={_PREFIX or '<default api_key[:8]>'} cookie={_COOKIE_SRC}")


if __name__ == "__main__":
    unittest.main()
