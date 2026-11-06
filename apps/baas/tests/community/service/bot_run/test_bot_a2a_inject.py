"""test bot a2a inject; focused regression tests and fixtures."""

from __future__ import annotations

import asyncio

from a2a_test_support_base import (
    LiveServer,
    _connect_async,
    _run,
)
from a2a_test_support_inject import (
    _accepted_inject,
    _inject_scenario,
    _inject_status_responder,
    make_inject_app,
)
from fastapi.responses import JSONResponse


def test_chat_inject_bare_string_error_body_no_crash():
    """200 + JSON 裸串 body：isinstance 守卫 + 原文进 message。"""
    app, _ = make_inject_app(
        lambda body: JSONResponse('"inject refused"', status_code=200)
    )
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert "inject refused" in out["error"]["message"]


def test_chat_inject_string_error_field_no_crash():
    """200 + error 字段是字符串：error 三键守卫，原文进 message。"""
    app, _ = make_inject_app(
        lambda body: JSONResponse(
            {"jsonrpc": "2.0", "id": body["id"], "error": "inject refused"},
            status_code=200,
        )
    )
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "a2a_error"
    assert "inject refused" in out["error"]["message"]


def test_chat_inject_rejects_attachments_without_wire_side_effects():
    app, state = make_inject_app(_accepted_inject)
    srv = LiveServer(app)
    try:

        async def scenario():
            client = await _connect_async(srv.base)
            out = await client.chat_inject(
                session_key="s",
                message="x",
                attachments=[{"kind": "screenshot"}],
            )
            await client.close()
            return out

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["raise_"] is False, "纯数据错误——剖面由编排层收敛"
    assert out["error"]["code"] == "ATTACHMENTS_UNSUPPORTED"
    assert state["bodies"] == [], "附件请求绝不得静默 wire"


def test_chat_inject_flattens_metadata_carries_token_and_parts():
    app, state = make_inject_app(_accepted_inject)
    srv = LiveServer(app)
    try:

        async def scenario():
            return await _inject_scenario(
                srv,
                message={"content": [{"type": "text", "text": "补上下文"}]},
                timeout_ms=1500,
                auth_token="tok-inj",
                chat_metadata={"source": "e2e"},
            )

        out = _run(scenario())
    finally:
        srv.stop()
    assert out["ok"] is True, out
    assert out["status"] == "accepted"
    assert out["a2a"] is True
    body = state["bodies"][0]
    assert body["method"] == "InjectContext"
    assert body["jsonrpc"] == "2.0"
    assert body["params"]["contextId"] == "s-inj"
    assert body["params"]["message"]["parts"] == [{"text": "补上下文"}], (
        f"dict message 的 content 必须展平成 parts: {body}"
    )
    meta = body["params"]["metadata"]
    assert meta["source"] == "e2e"
    assert meta["timeoutMs"] == "1500"
    assert meta["traceId"] == body["id"], "traceId 必须回填请求 id"
    assert state["headers"]["x-iam-token"] == "tok-inj"


def test_chat_inject_non_structured_messages_fall_back_to_string():
    message = {"content": "plain"}
    app, state = make_inject_app(_accepted_inject)
    srv = LiveServer(app)
    try:

        async def scenario():
            first = await _inject_scenario(
                srv, message=message
            )  # dict 但 content 非 parts 列表
            second = await _inject_scenario(srv, message=42)  # 完全非结构化
            await asyncio.sleep(0)
            return first, second

        first, second = _run(scenario())
    finally:
        srv.stop()
    assert first["ok"] is True and second["ok"] is True
    parts = [b["params"]["message"]["parts"] for b in state["bodies"]]
    assert parts == [
        [{"text": str(message)}],  # content 非 list → str(message) 兜底
        [{"text": "42"}],  # 非 dict → str 兜底
    ], f"非结构化 message 必须显式字符串化，不得炸不得静默: {parts}"


def test_chat_inject_idempotent_statuses_are_ok():
    for status in ("accepted", "duplicate_accepted", "duplicate_in_progress"):
        app, _ = make_inject_app(_inject_status_responder(status))
        srv = LiveServer(app)
        try:
            out = _run(_inject_scenario(srv))
        finally:
            srv.stop()
        assert out["ok"] is True, f"{status} 注入被误判失败: {out}"
        assert out["status"] == status


def test_chat_inject_unknown_status_is_not_ok():
    app, _ = make_inject_app(_inject_status_responder("weird"))
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False, "未知 inject 状态不得凑成功"
    assert out["status"] == "weird"
    assert out["a2a"] is True


def test_chat_inject_http_error_face_leaks_status():
    app, _ = make_inject_app(
        lambda body: JSONResponse({"error": "overloaded"}, status_code=503)
    )
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "HTTP_503"
    assert "overloaded" in out["error"]["message"]


def test_chat_inject_engine_error_is_relayed_with_reason():
    def responder(body: dict) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "error": {"message": "engine busy", "data": {"reason": "inject_conflict"}},
        }

    app, _ = make_inject_app(responder)
    srv = LiveServer(app)
    try:
        out = _run(_inject_scenario(srv))
    finally:
        srv.stop()
    assert out["ok"] is False
    assert out["error"]["code"] == "inject_conflict", "引擎 reason 必须保留"
    assert out["error"]["message"] == "engine busy"
