import asyncio

import httpx
import pytest

from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiAuthError, OpenApiBotAdapter, OpenApiServerError, parse_bot_id,
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
    return OpenApiBotAdapter(_Key(), http_client=client)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_parse_bot_id():
    assert parse_bot_id("bot9:ent1") == ("bot9", "ent1")


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
    assert rid == "mid_77"


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
