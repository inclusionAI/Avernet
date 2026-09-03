import asyncio

import httpx
import pytest

from agentclaw.community.core.task.task_runner.client.open_api_bot_adapter import (
    OpenApiAuthError, OpenApiBotAdapter, OpenApiError, OpenApiServerError, OpenApiTimeoutError, parse_bot_id,
)


class _Key:
    api_key = "ak1234567890"
    api_key_prefix = "ak12345678"
    base_url = "http://b:8890"
    cookie = "sess=1"
    referer = "http://b/"


def _adapter(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://b:8890")
    return OpenApiBotAdapter(_Key(), http_client=client, ensure_grant=True)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_parse_bot_id():
    assert parse_bot_id("bot9:ent1") == ("bot9", "ent1")


class _KeyNoPrefix:
    api_key = "ak1234567890"
    api_key_prefix = ""    # 未设置 → 回落取 api_key 前 10 位
    base_url = "http://b:8890"
    cookie = "sess=1"
    referer = "http://b/"


def test_ensure_grant_falls_back_to_api_key_prefix_when_unset():
    # api_key_prefix 为空时,URL 用 api_key 前 N 位(默认 10,与 _Key.ak12345678 约定一致)作 prefix
    seen: dict = {}

    def h(req):
        if req.method == "GET" and req.url.path.endswith("/allowed-bots"):
            seen["get_path"] = req.url.path
            return httpx.Response(200, json={"data": {"allowed_bots": ["bot9:ent1"]}})
        return httpx.Response(404)

    transport = httpx.MockTransport(h)
    client = httpx.AsyncClient(transport=transport, base_url="http://b:8890")
    a = OpenApiBotAdapter(_KeyNoPrefix(), http_client=client, ensure_grant=True)
    _run(a.ensure_grant("bot9:ent1"))  # 已 allowed → 不走 grant
    assert seen["get_path"] == "/api/v1/api-keys/ak123456/allowed-bots"


def test_ensure_grant_already_allowed():
    allowed = {"data": {"allowed_bots": ["bot9:ent1"]}}

    def h(req):
        return httpx.Response(200, json=allowed)

    a = _adapter(h)
    _run(a.ensure_grant("bot9:ent1"))  # 不抛


def test_ensure_grant_grants_when_missing():
    state = {"allowed": False}

    def h(req):
        if req.url.path.endswith("/allowed-bots") and req.method == "GET":
            return httpx.Response(200, json={"data": {"allowed_bots": [] if not state["allowed"] else ["bot9:ent1"]}})
        if req.url.path.endswith("/grant") and req.method == "POST":
            assert "sess=1" in req.headers.get("cookie", "")      # 登录态
            assert req.headers.get("referer") == "http://b/"
            state["allowed"] = True
            return httpx.Response(200, json={"data": {"bot_id": "bot9:ent1"}})
        return httpx.Response(404)

    _run(_adapter(h).ensure_grant("bot9:ent1"))
    assert state["allowed"] is True


def test_ensure_grant_403_raises_auth():
    def h(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": {"allowed_bots": []}})
        return httpx.Response(403)

    with pytest.raises(OpenApiAuthError):
        _run(_adapter(h).ensure_grant("bot9:ent1"))


def test_send_message_returns_run_id_and_uses_bearer():
    def h(req):
        assert req.url.path == "/openapi/v1/messages"
        assert req.headers["authorization"] == "Bearer ak1234567890"
        body = req.read()
        assert b'"bot_id":"bot9:ent1"' in body
        return httpx.Response(200, json={"data": {"message_id": "mid_77"}})

    rid = _run(_adapter(h).send_message(bot_id="bot9:ent1", message="hi", metadata={}))
    assert rid.run_id == "mid_77"
    assert rid.session_id is None


def test_send_message_forwards_session_id_when_present():
    def h(req):
        return httpx.Response(200, json={"data": {"message_id": "mid_77", "session_id": "s_99"}})

    rid = _run(_adapter(h).send_message(bot_id="bot9:ent1", message="hi", metadata={}))
    assert rid.run_id == "mid_77"
    assert rid.session_id == "s_99"


def test_get_run_status_case_insensitive():
    def h(req):
        assert req.url.path == "/openapi/v1/messages/mid_77"
        return httpx.Response(200, json={"data": {"status": "COMPLETED", "result": {"content": "x"}}})

    d = _run(_adapter(h).get_run("mid_77"))
    assert d["status"] == "COMPLETED"


def test_server_error_raises():
    def h(req):
        return httpx.Response(500)

    with pytest.raises(OpenApiServerError):
        _run(_adapter(h).get_run("mid_77"))


def test_send_and_wait_returns_completed_run():
    calls = {"n": 0}

    def h(req):
        if req.url.path == "/openapi/v1/messages" and req.method == "POST":
            return httpx.Response(200, json={"data": {"message_id": "mid_1"}})
        if req.url.path.endswith("/allowed-bots") and req.method == "GET":
            return httpx.Response(200, json={"data": {"allowed_bots": ["bot9:ent1"]}})
        if req.url.path.startswith("/openapi/v1/messages/mid") and req.method == "GET":
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, json={"data": {"status": "RUNNING"}})
            return httpx.Response(200, json={"data": {"status": "COMPLETED", "result": {"content": "ans"}}})
        return httpx.Response(404)

    a = _adapter(h)
    run = a.send_and_wait(bot_id="bot9:ent1", message="hi", timeout=2.0, poll_interval=0.001)
    assert run["status"] == "COMPLETED"
    assert run["result"]["content"] == "ans"
    assert calls["n"] >= 2  # 先 RUNNING 再 COMPLETED,至少轮询 2 次


def test_send_and_wait_timeout_raises():
    def h(req):
        if req.url.path == "/openapi/v1/messages" and req.method == "POST":
            return httpx.Response(200, json={"data": {"message_id": "mid_1"}})
        if req.url.path.endswith("/allowed-bots") and req.method == "GET":
            return httpx.Response(200, json={"data": {"allowed_bots": ["bot9:ent1"]}})
        if req.url.path.startswith("/openapi/v1/messages/mid") and req.method == "GET":
            return httpx.Response(200, json={"data": {"status": "RUNNING"}})
        return httpx.Response(404)

    a = _adapter(h)
    with pytest.raises(OpenApiTimeoutError):
        a.send_and_wait(bot_id="bot9:ent1", message="hi", timeout=0.05, poll_interval=0.001)


def test_send_and_wait_failed_returns_run():
    def h(req):
        if req.url.path == "/openapi/v1/messages" and req.method == "POST":
            return httpx.Response(200, json={"data": {"message_id": "mid_1"}})
        if req.url.path.endswith("/allowed-bots") and req.method == "GET":
            return httpx.Response(200, json={"data": {"allowed_bots": ["bot9:ent1"]}})
        if req.url.path.startswith("/openapi/v1/messages/mid") and req.method == "GET":
            return httpx.Response(200, json={"data": {"status": "FAILED", "error": "boom"}})
        return httpx.Response(404)

    a = _adapter(h)
    run = a.send_and_wait(bot_id="bot9:ent1", message="hi", timeout=2.0, poll_interval=0.001)
    assert run["status"] == "FAILED"
    assert run["error"] == "boom"


# ===== ACE 网关后的真实 host 需在 Bearer 之外带 Cookie/Referer;adapter 各请求须透传 key 的 cookie/referer =====
def test_send_message_forwards_cookie_and_referer():
    seen: dict = {}

    def h(req):
        if req.url.path == "/openapi/v1/messages":
            seen["cookie"] = req.headers.get("cookie")
            seen["referer"] = req.headers.get("referer")
            return httpx.Response(200, json={"data": {"message_id": "mid_ck"}})
        return httpx.Response(404)

    rid = _run(_adapter(h).send_message(bot_id="bot9:ent1", message="hi", metadata={}))
    assert rid.run_id == "mid_ck"
    assert seen["cookie"] == "sess=1"
    assert seen["referer"] == "http://b/"


def test_get_run_forwards_cookie_and_referer():
    seen: dict = {}

    def h(req):
        if req.url.path == "/openapi/v1/messages/mid_77":
            seen["cookie"] = req.headers.get("cookie")
            seen["referer"] = req.headers.get("referer")
            return httpx.Response(200, json={"data": {"status": "COMPLETED"}})
        return httpx.Response(404)

    _run(_adapter(h).get_run("mid_77"))
    assert seen["cookie"] == "sess=1"
    assert seen["referer"] == "http://b/"


def test_ensure_grant_get_forwards_cookie_and_referer():
    seen: dict = {}

    def h(req):
        if req.method == "GET" and req.url.path.endswith("/allowed-bots"):
            seen["cookie"] = req.headers.get("cookie")
            seen["referer"] = req.headers.get("referer")
            return httpx.Response(200, json={"data": {"allowed_bots": ["bot9:ent1"]}})
        return httpx.Response(404)

    _run(_adapter(h).ensure_grant("bot9:ent1"))
    assert seen["cookie"] == "sess=1"
    assert seen["referer"] == "http://b/"


# ===== 业务信封校验:HTTP 200 但 code!=0 / 无 message_id 不可当成功吞成 run_id=None(否则 get_run(None) 报误导 404) =====
def test_send_message_raises_when_no_message_id():
    def h(req):
        # HTTP 200 但无 message_id(模拟 ACE 登录门回 USER_NOT_LOGIN、或未授权回空 data)
        return httpx.Response(200, json={"code": 0, "data": {}})

    with pytest.raises(OpenApiError):
        _run(_adapter(h).send_message(bot_id="bot9:ent1", message="hi", metadata={}))


def test_send_message_raises_on_nonzero_business_code():
    def h(req):
        # HTTP 200 但业务 code 非 0(如 bot 未在 allowed_bots → 403 业务信封)
        return httpx.Response(200, json={"code": 40301, "message": "bot not in allowed_bots", "data": None})

    with pytest.raises(OpenApiError):
        _run(_adapter(h).send_message(bot_id="bot9:ent1", message="hi", metadata={}))


def test_ensure_grant_skipped_by_default():
    # 默认 ensure_grant=False(OOB 预授权模式):不发 allowed-bots GET/grant,直接 return。
    # admin allowed-bots 端点只认 Human Cookie,corp 无 cookie 时 Bearer-only 会 500;
    # prod 假定 bot 已 OOB 预授权 → ensure_grant 默认跳过,直进 send_message(dispatch 端点认 Bearer)。
    seen: dict = {}

    def h(req):
        seen["path"] = req.url.path
        return httpx.Response(200, json={"data": {"allowed_bots": []}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(h), base_url="http://b:8890")
    a = OpenApiBotAdapter(_Key(), http_client=client)  # 默认 ensure_grant=False
    _run(a.ensure_grant("bot9:ent1"))
    assert "allowed-bots" not in seen.get("path", ""), f"默认应跳过 ensure_grant,却发了 {seen.get('path')!r}"




def test_owned_client_pinned_reused_same_loop_isolated_across_loops():
    """跨事件循环复用同一 httpx.AsyncClient 会抛 `RuntimeError: ... bound to a different event loop`
    (生产:harness/scheduler/recovery 经 asyncio.run,HTTP 经 new_event_loop,共同驱动同一 adapter)。
    _client_for_current_loop 把自建 client pin 到首个 loop(同 loop 复用,保留连接池),其它 loop 用一次性
    client(对齐 BcsHttpAdapter 同款修复)。
    """
    import asyncio

    a = OpenApiBotAdapter(_Key())  # 未注入 client → 自建,_owns_client=True(生产路径)

    async def take():
        async with a._client_for_current_loop() as c:
            return c

    # 首个持久 loop:pin,多次取复用同一持久 client(保留连接池)
    loop_a = asyncio.new_event_loop()
    c_a1 = c_a2 = None
    try:
        c_a1 = loop_a.run_until_complete(take())
        c_a2 = loop_a.run_until_complete(take())
    finally:
        loop_a.close()
    assert c_a1 is c_a2, "同一(首个)loop 内应复用 pinned 持久 client"

    # 另一 loop(与首个不同):一次性独立 client,不得与首个 loop 的 client 共享
    loop_b = asyncio.new_event_loop()
    c_b = None
    try:
        c_b = loop_b.run_until_complete(take())
    finally:
        loop_b.close()
    assert c_b is not c_a1, "跨 loop 不得共享连接池(否则抛 different event loop)"
    assert a._client_loop is not None


def test_send_message_uses_loop_compatible_client_across_event_loops(monkeypatch):
    """Real adapter requests must not reuse an owned AsyncClient across loops."""
    import agentclaw.community.core.task.task_runner.client.open_api_bot_adapter as module

    created = []
    used = []

    class _LoopBoundClient:
        def __init__(self, **kwargs):
            self.base_url = httpx.URL(kwargs.get("base_url", "http://b:8890"))
            self.timeout = kwargs.get("timeout")
            self._bound_loop = None
            created.append(self)

        async def post(self, *args, **kwargs):
            loop = asyncio.get_running_loop()
            if self._bound_loop is None:
                self._bound_loop = loop
            elif self._bound_loop is not loop:
                raise RuntimeError("client used from a different event loop")
            used.append(self)
            return httpx.Response(
                200,
                json={"data": {"message_id": "run-1"}},
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(module.httpx, "AsyncClient", _LoopBoundClient)
    adapter = OpenApiBotAdapter(_Key())

    async def send():
        return await adapter.send_message(bot_id="bot9:ent1", message="hi", metadata={})

    assert _run(send()).run_id == "run-1"
    assert _run(send()).run_id == "run-1"
    assert len(created) == 2
    assert used[0] is created[0]
    assert used[1] is created[1]


def test_injected_client_kept_across_loops():
    """注入的 client(测试 MockTransport)由调用方管理 loop 绑定;_client_for_current_loop 原样返回同一
    client,跨 loop 不变、永不重建。"""
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": {}}))
    injected = httpx.AsyncClient(transport=transport, base_url="http://b:8890")
    a = OpenApiBotAdapter(_Key(), http_client=injected, ensure_grant=True)

    async def take():
        async with a._client_for_current_loop() as c:
            return c

    assert _run(take()) is injected
    assert _run(take()) is injected  # 跨 loop 仍是注入的同一 client
