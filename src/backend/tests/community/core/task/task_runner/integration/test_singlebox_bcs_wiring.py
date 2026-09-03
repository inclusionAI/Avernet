"""singlebox 环境 BCS 端口接线测试:复用 BcsHttpAdapter + 本地 LocalBcsTokenProvider。

本地 BCS(Rust :21000)与生产 BCS 同 REST、`require_authentication=false`,
HMAC `X-ECB-*` 头被本地忽略 → singlebox 直接使用本地 BCS 凭据。
本测覆盖:本地凭据取值、_resolve_ports 装配真实 BcsHttpAdapter、create_group 契约。
"""
from __future__ import annotations

import asyncio

import httpx

from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupRequest, BcsHttpAdapter,
)
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    BcsTokenProvider, LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.singlebox_bcs_adapter import (
    SingleboxBcsAdapter,
)
from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    SingleboxEngineAdapter,
)
from agentclaw.community.di.modules.task_module import TaskModule


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _adapter(transport, base_url="http://b:21000"):
    return SingleboxBcsAdapter(
        LocalBcsTokenProvider(base_url=base_url),
        http_client=httpx.AsyncClient(transport=transport, base_url=base_url),
    )


def test_local_bcs_token_provider_from_env(monkeypatch):
    monkeypatch.setenv("SINGLEBOX_BCS_URL", "http://bcs.local:21099")
    p = LocalBcsTokenProvider.from_env()
    assert p.base_url == "http://bcs.local:21099"
    assert isinstance(p, BcsTokenProvider)  # runtime_checkable 契约:token/secret/base_url 三属性齐

    monkeypatch.delenv("SINGLEBOX_BCS_URL", raising=False)
    assert LocalBcsTokenProvider.from_env().base_url == "http://localhost:21000"


def test_resolve_ports_singlebox_uses_singlebox_bcs_adapter(monkeypatch):
    """singlebox 下 _resolve_ports 的 bcs 端口应装配 SingleboxBcsAdapter(指向本地 BCS),而非 _DoubleBcsClient。"""
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("SINGLEBOX_BCS_URL", "http://localhost:21000")
    monkeypatch.setenv("SINGLEBOX_USER_ID", "35983")
    bot, bcs = TaskModule._resolve_ports()
    try:
        assert isinstance(bot, SingleboxEngineAdapter)
        assert isinstance(bcs, SingleboxBcsAdapter), "singlebox bcs 端口应为 SingleboxBcsAdapter"
        assert isinstance(bcs, BcsHttpAdapter)  # 继承 BcsHttpAdapter
        client = bcs._client  # type: ignore[attr-defined]
        assert client.base_url.host == "localhost"
        assert client.base_url.port == 21000
    finally:
        _run(bot._aclose())  # 停 SingleboxEngineAdapter 后台线程 + 关 http client


def test_create_group_maps_local_id_to_group_id():
    """本地 /groups 用 "id"(非 "group_id")返回群 id;adapter 应映射成 BcsCreateGroupResult.group_id。"""
    seen: dict = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        # 本地 group_detail_to_create_json 形状:id + session_id,无 group_id/run_id/definition_ref
        return httpx.Response(200, json={"id": "g_local", "session_id": "s1",
                                         "driver_bot": "bot_ceo", "participants": ["bot_p"],
                                         "created": True})

    res = _run(_adapter(httpx.MockTransport(h)).create_group(BcsCreateGroupRequest(
        driver_bot="bot_ceo", participants=[{"bot_uuid": "bot_p", "role": "consultant"}],
        group_strategy="chat",
    )))
    assert seen["path"] == "/groups"
    assert res.group_id == "g_local"   # id → group_id
    assert res.session_id == "s1"
    assert res.run_id is None and res.definition_ref is None


def test_create_group_state_machine_body_sets_start_initial_run_false():
    """SM 群:body 应 group_strategy=state_machine + start_initial_run=False(对齐 BcsHttpAdapter)。"""
    seen: dict = {}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json
        seen["body"] = _json.loads(req.content) if req.content else {}
        return httpx.Response(200, json={"id": "g_sm", "session_id": None})

    res = _run(_adapter(httpx.MockTransport(h)).create_group(BcsCreateGroupRequest(
        driver_bot="bot_ceo", participants=[{"bot_uuid": "bot_p", "role": "consultant"}],
        group_strategy="state_machine",
        collaboration_definition_yaml="states: [s1, s2]",
    )))
    assert res.group_id == "g_sm"
    assert seen["body"]["group_strategy"] == "state_machine"
    assert seen["body"]["start_initial_run"] is False
    assert seen["body"]["collaboration_definition_yaml"] == "states: [s1, s2]"


def test_create_group_forwards_caller_bot_token_as_bearer():
    """SingleboxBcsAdapter 手写镜像 BcsHttpAdapter.create_group,须同样透传 caller_bot_token 为 Authorization: Bearer。"""
    seen: dict = {}

    def h(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"id": "g_b", "session_id": None})

    _run(_adapter(httpx.MockTransport(h)).create_group(BcsCreateGroupRequest(
        driver_bot="bot_ceo", participants=[{"bot_uuid": "bot_p"}],
        caller_bot_token="drv-tok",
    )))
    assert seen["auth"] == "Bearer drv-tok"
