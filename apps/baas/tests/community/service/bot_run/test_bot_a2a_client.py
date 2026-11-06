"""test bot a2a client; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
)
from a2a_test_support_base import (
    failed_server as failed_server,
)
from a2a_test_support_base import (
    gate_server as gate_server,
)
from a2a_test_support_base import (
    stream_server as stream_server,
)
from a2a_test_support_errors import (
    _make_broken_body_server,
    make_stringified_error_app,
)

from secbaas.community.core.service.bot_run._bot_a2a_client import (
    BotA2aClient,
)


def test_ack_returns_before_final(stream_server):
    async def scenario():
        client = await _connect_async(stream_server.base)
        t0 = time.monotonic()
        ack = await client.chat_send(session_key="s", message="hi")
        elapsed = time.monotonic() - t0
        assert ack["ok"] is True and ack["a2a"] is True
        # 真流式 fixture 的 final_delay=0.5s——ack 必须显著早于
        assert elapsed < 0.4, f"ack blocked {elapsed:.2f}s (mock final at 0.5s)"
        await asyncio.sleep(1.0)  # body task 消费完
        await client.close()

    _run(scenario())


def test_first_delta_before_final_with_barrier(stream_server):
    """R17-1: 事件屏障——delta 到达时 final 必须尚未出现（真流式专属断言）。"""
    received: list[tuple[str, dict]] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event("chat", lambda p, _n="chat": received.append((_n, p)))
        client.on_event("agent", lambda p, _n="agent": received.append((_n, p)))
        client.on_event(
            "mode_transition.resolved",
            lambda p, _n="mode_transition.resolved": received.append((_n, p)),
        )  # R17-2 路由验证
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(0.25)  # final_delay=0.5s 之内
        states = {p.get("state") for _, p in received}
        return states

    states = _run(scenario())
    assert "delta" in states, f"delta missing at barrier: {states}"
    assert "final" not in states, f"final leaked before barrier: {states}"


def test_mode_transition_dispatches_to_original_event_name(stream_server):
    mt: list[dict] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event("mode_transition.resolved", lambda p: mt.append(p))
        client.on_event("agent", lambda p: mt.append({"__agent__": p}))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    assert any(p.get("mode") == "plan" for p in mt), (
        f"mode_transition.resolved 未按原事件名路由: {mt}"
    )


def test_double_final_dedup_and_content_survived(stream_server):
    finals: list[dict] = []

    async def scenario():
        client = await _connect_async(stream_server.base)
        client.on_event(
            "chat", lambda p: finals.append(p) if p.get("state") == "final" else None
        )
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    assert len(finals) == 1, f"terminal emitted {len(finals)}x"
    text = finals[0]["message"]["content"][0]["text"]
    assert text == "你好，完整终稿"


def test_failed_is_error_not_final(failed_server):
    seen: list[tuple[str, dict]] = []

    async def scenario():
        client = await _connect_async(failed_server.base)
        client.on_event("chat", lambda p: seen.append(("chat", p)))
        await client.chat_send(session_key="s", message="hi")
        await asyncio.sleep(1.0)
        await client.close()

    _run(scenario())
    errors = [p for _, p in seen if p.get("state") == "error"]
    finals = [p for _, p in seen if p.get("state") == "final"]
    assert errors and not finals, (
        f"FAILED must be error, not final; seen={[p['state'] for _, p in seen]}"
    )
    assert "engine broke" in errors[0].get("errorMessage", "")


def test_gate_rejection_not_ok(gate_server):
    async def scenario():
        client = await _connect_async(gate_server.base)
        return await client.chat_send(session_key="s", message="hi")

    out = _run(scenario())
    assert out["ok"] is False
    assert out["error"]["code"] == "auth_required"


def test_bare_string_error_body_surfaces_raw_not_crash():
    """HTTP 502 + JSON 裸串 body：不得炸 AttributeError，原文必须进 message。"""
    srv = LiveServer(make_stringified_error_app("upstream refused", 502))
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            out = await client.chat_send(session_key="s", message="hi")
            await client.close()
            return out

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_502", "解析不出对象时回退真实状态码"
    assert "upstream refused" in out["error"]["message"], "原始错误不得被盖住"


def test_error_body_read_failure_returns_structured_and_closes():
    """P2 回归钉：错误响应头已到手（502），但错误体传输中断。

    读体不得炸出 chat_send（上一版 aread 曾被移出 try）——必须结构化
    返回 HTTP_502 + 可分诊 message；读挂的临时 http client 未登记进
    _runs、close() 兜不住它，必须就地关闭。

    真实 socket 断连触发的异常形态在 RemoteProtocolError（有文本）与
    空串 ReadError 之间摆动——message 非空由实现层的空文本回退保证
    （见 test_error_body_read_failure_empty_exc_message_falls_back）。
    """

    srv = _make_broken_body_server()
    # httpx AsyncClient.__aexit__ 直接关 transport、不经 aclose()——connect
    # 阶段的 with 块不会计入。关闭断言只针对 chat_send 阶段创建的临时
    # client（read 失败的孤儿正是它——未登记进 _runs，close() 兜不住）。
    made: list[dict[str, bool]] = []
    orig_http = BotA2aClient._http

    def counting_http(self, *, timeout=None):
        created = orig_http.__get__(self, BotA2aClient)(timeout=timeout)
        entry: dict[str, bool] = {"closed": False}
        made.append(entry)
        real_aclose = created.aclose

        async def counting_aclose():
            entry["closed"] = True
            await real_aclose()

        created.aclose = counting_aclose
        return created

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(BotA2aClient, "_http", counting_http)

    async def scenario():
        client = await _connect_async(srv.base)
        pre = len(made)
        out = await client.chat_send(session_key="s", message="hi")
        chat_phase = made[pre:]  # chat_send 创建的全部临时 client
        await client.close()
        return out, chat_phase

    try:
        out, chat_phase = _run(scenario())
    finally:
        srv.stop()
        monkeypatch.undo()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_502", "状态码来自已到手的响应头"
    assert out["error"]["message"], "空串传输异常须回退类名，不得为空"
    assert chat_phase, "chat_send 必须经由 _http 建临时 client"
    assert all(e["closed"] for e in chat_phase), "读挂的临时 client 必须就地关闭"


def test_error_body_read_failure_survives_resp_aclose_error():
    """读体失败后 resp.aclose 再炸（best-effort 不得反噬清理）：
    client 关闭与结构化返回都必须完成。"""
    srv = _make_broken_body_server()

    async def scenario():
        client = await _connect_async(srv.base)
        # connect 后再下补丁：card 探针自身的响应读取也走 Response.aclose，
        # 全局炸会击穿 A2aUnavailableError 门（灰度回退承诺）。
        real_aclose = httpx.Response.aclose

        async def bomb_aclose(self):
            raise RuntimeError("aclose also broken")

        httpx.Response.aclose = bomb_aclose
        try:
            out = await client.chat_send(session_key="s", message="hi")
        finally:
            httpx.Response.aclose = real_aclose
        await client.close()
        return out

    try:
        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_502"
    assert out["error"]["message"], "传输异常文本仍然要透出"


def test_error_body_read_failure_empty_exc_message_falls_back():
    """空文本传输异常（ReadError("")/ReadTimeout("")）确定性复现：
    message 必须回退到异常类名，保证恒有可分诊内容（评审 P2）。

    http_transport_factory + MockTransport 注入，响应头 502 到手、
    读体时流抛空文本异常——不走真实 socket，形态完全确定。
    """

    card = {
        "supportedInterfaces": [
            {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ]
    }

    def _client_for(exc: Exception) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if "agent-card.json" in request.url.path:
                return httpx.Response(200, json=card)

            async def exploding_body():
                yield b""
                raise exc

            return httpx.Response(
                502,
                headers={"content-type": "application/json"},
                content=exploding_body(),
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def scenario(exc: Exception) -> dict:
        client = BotA2aClient(
            base_url="http://testserver",
            http_transport_factory=lambda: _client_for(exc),
        )
        hello = await client.connect()
        assert hello["protocol"] == 1
        out = await client.chat_send(session_key="s", message="hi")
        await client.close()
        return out

    for exc, name in [
        (httpx.ReadError(""), "ReadError"),
        (httpx.ReadTimeout(""), "ReadTimeout"),
    ]:
        out = _run(scenario(exc))
        assert out["ok"] is False, f"{name}: 读体失败必须结构化返回"
        assert out["error"]["code"] == "HTTP_502", f"{name}: 状态码来自已到手的响应头"
        assert out["error"]["message"] == name, f"{name}: 空文本必须回退异常类名"


def test_string_error_field_body_surfaces_raw_not_crash():
    """200 + error 字段是字符串（非对象）：同款两级守卫，原文可见。"""
    srv = LiveServer(
        make_stringified_error_app(
            {"jsonrpc": "2.0", "id": 1, "error": "gate exploded"}
        )
    )
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            out = await client.chat_send(session_key="s", message="hi")
            await client.close()
            return out

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_200", "error 非对象时 reason 不可得"
    assert "gate exploded" in out["error"]["message"]
