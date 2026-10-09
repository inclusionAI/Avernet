import asyncio
import json

import httpx
import pytest

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsClientError, BcsClientRequestError, BcsCreateGroupRequest, BcsHttpAdapter,
    BcsServerError,
)


class _Tok:
    token = "drv"
    secret = "s3c"
    base_url = "http://bcs"


def _adapter(handler):
    return BcsHttpAdapter(_Tok(), http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                                                base_url="http://bcs"))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_create_group_chat_signs_and_sends_idempotency():
    seen = {}

    def h(req):
        seen["method"] = req.method
        seen["path"] = req.url.path
        seen["sig"] = req.headers.get("X-ECB-Signature")
        seen["tok"] = req.headers.get("X-ECB-Token")
        seen["idem"] = req.headers.get("Idempotency-Key")
        import hashlib
        import hmac
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


def test_create_group_forwards_routing_policy_and_label_when_set():
    import json

    seen = {}

    def h(req):
        seen["body"] = json.loads(req.read())
        return httpx.Response(200, json={"group_id": "g9", "session_id": None})

    req = BcsCreateGroupRequest(
        driver_bot="drv",
        participants=[{"bot_uuid": "drv"}],
        routing_policy={"default_bot_final_delivery": "inject_observers"},
        label="task-t1-c1",
    )
    res = _run(_adapter(h).create_group(req))
    assert res.group_id == "g9"
    assert seen["body"]["routing_policy"] == {"default_bot_final_delivery": "inject_observers"}
    assert seen["body"]["label"] == "task-t1-c1"


def test_create_group_omits_routing_policy_and_label_when_none():
    import json

    seen = {}

    def h(req):
        seen["body"] = json.loads(req.read())
        return httpx.Response(200, json={"group_id": "g9", "session_id": None})

    req = BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}])
    _run(_adapter(h).create_group(req))
    assert "routing_policy" not in seen["body"]
    assert "label" not in seen["body"]


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
    def h(req):
        return httpx.Response(500)

    with pytest.raises(BcsServerError):
        _run(_adapter(h).get_group("g1"))


def test_client_4xx_raises():
    def h(req):
        return httpx.Response(400)

    with pytest.raises(BcsClientRequestError):
        _run(_adapter(h).get_group("g1"))


# ===== BCS 建群参考 ocb:可选带 driver-bot Bearer(Authorization)做 caller 身份;HMAC X-ECB-* 照常 =====
def test_create_group_forwards_caller_bot_token_as_bearer():
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        seen["token"] = req.headers.get("X-ECB-Token")
        seen["sig"] = req.headers.get("X-ECB-Signature")
        return httpx.Response(200, json={"group_id": "g_bearer"})

    req = BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}],
                                caller_bot_token="drv-session-token")
    res = _run(_adapter(h).create_group(req))
    assert res.group_id == "g_bearer"
    assert seen["auth"] == "Bearer drv-session-token"
    assert seen["token"] == "drv"          # HMAC X-ECB-* 照常签发
    assert seen["sig"] is not None


def test_create_group_omits_bearer_when_no_caller_bot_token():
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"group_id": "g_no_bearer"})

    req = BcsCreateGroupRequest(driver_bot="drv", participants=[{"bot_uuid": "drv"}])
    _run(_adapter(h).create_group(req))
    assert seen["auth"] is None             # 无 token 不发 Authorization


def test_owned_client_isolated_when_event_loop_changes(monkeypatch):
    import agentclaw.community.core.task.task_runner.client.bcs_http_adapter as module

    class _FakeClient:
        instances = []

        def __init__(self, *, base_url):
            self.base_url = base_url
            self.closed = False
            self.__class__.instances.append(self)

        async def request(self, method, path, **kwargs):
            return httpx.Response(200, json={"session": {"status": "completed"}})

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(module.httpx, "AsyncClient", _FakeClient)
    adapter = module.BcsHttpAdapter(_Tok())

    _run(adapter.get_group("g1"))
    _run(adapter.get_group("g2"))

    assert len(_FakeClient.instances) == 2
    assert _FakeClient.instances[1].closed is True


# ===== BCS A2A 单 bot 异步消息:POST /bots/{bot}:{owner}/chat-async =====
def test_send_message_a2a_posts_chat_async_with_colon_addressing():
    """A2A:目标按 ``{bot_id}:{owner_id}`` 寻址,Bearer 叠加在 X-ECB-* 之上,sessionId 透传。"""
    seen = {}

    def h(req):
        assert req.method == "POST"
        assert req.url.path == "/bots/20260825_p8e63hms:35983/chat-async"
        assert req.headers["authorization"] == "Bearer drv-session-token"
        assert req.headers.get("X-ECB-Token") == "drv"   # HMAC 服务签名照常
        seen["body"] = json.loads(req.read())
        return httpx.Response(202, json={
            "run_id": "run_a2a_1",
            "bot_uuid": "20260825_p8e63hms:35983",
            "session_id": "bcs-cli:task-20261009190011123-198568",
            "status": "pending",
            "expires_at_ms": 1791551286422,
        })

    rid = _run(_adapter(h).send_message_a2a(
        target_bot_id="20260825_p8e63hms",
        target_user_id="35983",
        message="hi",
        session_id="bcs-cli:task-20261009190011123-198568",
        caller_bot_token="drv-session-token",
    ))
    assert rid.run_id == "run_a2a_1"
    assert rid.session_id == "bcs-cli:task-20261009190011123-198568"
    assert seen["body"] == {
        "message": "hi",
        "responseMode": "after-last-tool-call",
        "sessionId": "bcs-cli:task-20261009190011123-198568",
    }


def test_send_message_a2a_omits_session_id_when_unset():
    def h(req):
        body = json.loads(req.read())
        assert "sessionId" not in body
        return httpx.Response(202, json={"run_id": "run_a2a_2", "session_id": None})

    rid = _run(_adapter(h).send_message_a2a(
        target_bot_id="bot-a", target_user_id="u1", message="hi",
        caller_bot_token="drv-session-token",
    ))
    assert rid.run_id == "run_a2a_2"
    assert rid.session_id is None


def test_send_message_a2a_without_token_sends_hmac_only():
    seen = {}

    def h(req):
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(202, json={"run_id": "run_a2a_3"})

    rid = _run(_adapter(h).send_message_a2a(
        target_bot_id="bot-a", target_user_id="u1", message="hi",
    ))
    assert rid.run_id == "run_a2a_3"
    assert seen["auth"] is None             # 无 token 不发 Authorization(BCS 侧 401 由调用方感知)


def test_send_message_a2a_rejects_payload_without_run_id():
    """网关登录门(ACE USER_NOT_LOGIN 等)可能回 202 但无 run_id → 业务失败直抛。"""
    def h(req):
        return httpx.Response(202, json={"buserviceErrorCode": "USER_NOT_LOGIN"})

    with pytest.raises(BcsClientError, match="run_id"):
        _run(_adapter(h).send_message_a2a(
            target_bot_id="bot-a", target_user_id="u1", message="hi",
            caller_bot_token="drv-session-token",
        ))


def test_send_message_a2a_maps_4xx_to_request_error():
    def h(req):
        return httpx.Response(404, json={"error": "Bot not found", "status": 404})

    with pytest.raises(BcsClientRequestError):
        _run(_adapter(h).send_message_a2a(
            target_bot_id="bot-a", target_user_id="u1", message="hi",
            caller_bot_token="drv-session-token",
        ))
