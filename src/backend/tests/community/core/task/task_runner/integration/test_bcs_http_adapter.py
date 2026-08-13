import asyncio

import httpx
import pytest

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsClientRequestError, BcsCreateGroupRequest, BcsHttpAdapter, BcsServerError,
)


class _Tok:
    token = "drv"; secret = "s3c"; base_url = "http://bcs"


def _adapter(handler):
    return BcsHttpAdapter(_Tok(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                                base_url="http://bcs"))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_create_group_chat_signs_and_sends_idempotency():
    seen = {}
    def h(req):
        seen["method"] = req.method; seen["path"] = req.url.path
        seen["sig"] = req.headers.get("X-ECB-Signature")
        seen["tok"] = req.headers.get("X-ECB-Token")
        seen["idem"] = req.headers.get("Idempotency-Key")
        import hmac, hashlib
        ts = req.headers["X-ECB-Timestamp"]
        exp = hmac.new(b"s3c", f"{ts}{req.method}{req.url.path}".encode(), hashlib.sha256).hexdigest()
        assert req.headers["X-ECB-Signature"] == exp
        return httpx.Response(200, json={"group_id": "g1", "session_id": None})
    req = BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}])
    res = _run(_adapter(h).create_group(req))
    assert res.group_id == "g1"
    assert seen["idem"] is not None
    assert seen["tok"] == "drv"


def test_create_group_state_machine_forces_strategy_and_start_false():
    seen = {}
    def h(req):
        seen["body"] = req.read().decode()
        return httpx.Response(200, json={"group_id": "g2", "definition_ref": {"id": "d1", "version": 1}})
    req = BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}],
                                group_strategy="state_machine",
                                collaboration_definition_yaml="kind: collab",
                                participant_bindings={"drv": {"source": "manual", "bot_ids": ["drv"]}},
                                start_initial_run=False)
    res = _run(_adapter(h).create_group(req))
    assert res.group_id == "g2"
    assert '"start_initial_run":false' in seen["body"].replace(" ", "")
    assert '"group_strategy":"state_machine"' in seen["body"].replace(" ", "")


def test_create_session_returns_session_id():
    def h(req):
        assert req.url.path == "/groups/g1/sessions"
        return httpx.Response(200, json={"session_id": "s1"})
    rid = _run(_adapter(h).create_session("g1", bootstrap_prompt="hi"))
    assert rid == "s1"


def test_get_group():
    def h(req):
        assert req.url.path == "/groups/g1"
        return httpx.Response(200, json={"session": {"status": "completed"}})
    d = _run(_adapter(h).get_group("g1"))
    assert d["session"]["status"] == "completed"


def test_get_session_messages_since_cursor():
    def h(req):
        assert "since_msg_id=m9" in str(req.url)
        return httpx.Response(200, json=[{"role": "assistant", "content": "ans"}])
    msgs = _run(_adapter(h).get_session_messages("s1", since_msg_id="m9"))
    assert msgs[0]["content"] == "ans"


def test_server_error_raises():
    def h(req): return httpx.Response(500)
    with pytest.raises(BcsServerError):
        _run(_adapter(h).get_group("g1"))


def test_client_4xx_raises():
    def h(req): return httpx.Response(400)
    with pytest.raises(BcsClientRequestError):
        _run(_adapter(h).get_group("g1"))
