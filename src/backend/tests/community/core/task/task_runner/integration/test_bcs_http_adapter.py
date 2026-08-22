import asyncio

import httpx
import pytest

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsClientRequestError, BcsCreateGroupRequest, BcsHttpAdapter, BcsServerError,
    BotTaskModeRoster,
)


class _Tok:
    token = "drv"
    secret = "s3c"
    base_url = "http://bcs"
    provider_id = "prov-1"
    provider_admin_token = "adm-tok"


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
    def h(req):
        return httpx.Response(500)

    with pytest.raises(BcsServerError):
        _run(_adapter(h).get_group("g1"))


def test_client_4xx_raises():
    def h(req):
        return httpx.Response(400)

    with pytest.raises(BcsClientRequestError):
        _run(_adapter(h).get_group("g1"))


def test_list_bots_by_task_modes_sends_bearer_and_maps_items():
    seen = {}

    def h(req):
        seen["path"] = req.url.path
        seen["auth"] = req.headers.get("authorization")
        seen["claim"] = req.url.params.get("task_claim_mode")
        seen["dream"] = req.url.params.get("task_dream_mode")
        seen["match"] = req.url.params.get("match")
        return httpx.Response(200, json={"items": [
            {"bot_id": "b1", "name": "n1", "env": "dev", "task_claim_mode": True, "task_dream_mode": False},
            {"bot_id": "b2", "name": "n2", "env": "dev", "task_claim_mode": True, "task_dream_mode": True},
        ]})

    roster = _run(_adapter(h).list_bots_by_task_modes(provider_id="prov-1", claim=True, dream=True, match="all"))
    assert seen["path"] == "/providers/prov-1/bots/by-task-modes"
    assert seen["auth"] == "Bearer adm-tok"
    assert seen["claim"] == "true"
    assert seen["dream"] == "true"
    assert seen["match"] == "all"
    assert roster == [
        BotTaskModeRoster(bot_id="b1", name="n1", env="dev", task_claim_mode=True, task_dream_mode=False),
        BotTaskModeRoster(bot_id="b2", name="n2", env="dev", task_claim_mode=True, task_dream_mode=True),
    ]


def test_list_bots_by_task_modes_omits_unset_toggles():
    seen = {}

    def h(req):
        seen["claim"] = req.url.params.get("task_claim_mode")
        seen["dream"] = req.url.params.get("task_dream_mode")
        seen["match"] = req.url.params.get("match")
        return httpx.Response(200, json={"items": []})

    roster = _run(_adapter(h).list_bots_by_task_modes(provider_id="prov-1"))
    assert seen["claim"] is None          # claim=None → 该开关不过滤,不发 query 参数
    assert seen["dream"] is None
    assert seen["match"] == "any"         # 默认 any
    assert roster == []


def test_list_bots_by_task_modes_401_raises():
    def h(req):
        assert req.headers.get("authorization") == "Bearer adm-tok"
        return httpx.Response(401, json={"error": "unauthorized", "status": 401})

    with pytest.raises(BcsClientRequestError):
        _run(_adapter(h).list_bots_by_task_modes(provider_id="prov-1", claim=True))


def test_owned_client_isolated_when_event_loop_changes(monkeypatch):
    import agentclaw.community.core.task.task_runner.integration.bcs_http_adapter as module

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
