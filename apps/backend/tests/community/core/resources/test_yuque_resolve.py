"""Tests for yuque_resolve — covers success + all structured error scenarios.

Each test mocks the HTTP layer (requests.post) and/or the DeviceContextResolver
to simulate the various failure points in the resolve pipeline.

Task 2.4 后:``_get_engine_url`` 内部走 ``resolver.resolve_for_bot(bot_id, user_id)``,
不再依赖 ``bot_service`` / ``device_service.get_device_connection_v2``。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
)
from agentclaw.community.core.resources.yuque_resolve import (
    YUQUE_RESOLVE_ENGINE_UNAVAILABLE,
    YUQUE_RESOLVE_FAILED,
    YUQUE_RESOLVE_INVALID_URL,
    YUQUE_RESOLVE_NO_BINDING,
    YUQUE_RESOLVE_PARSE_ERROR,
    YuqueResolveError,
    resolve_yuque_url,
)


def _make_resolver(conn_url="http://engine:20003", binding_id=42):
    """Create a mock DeviceContextResolver returning a baas DeviceContext."""
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = DeviceContext(
        provider="baas",
        conn_info={
            "url": conn_url,
            "headers": {"X-Token": "test"},
            "binding_id": binding_id,
        },
        binding_id=binding_id,
        bot_id="bot1",
        user_id="user1",
    )
    return resolver


def _mcp_ok_response(doc_id=12345, book_id=6789, doc_type="Doc", title="Test Doc"):
    """Build a successful MCP call-tool JSON response body."""
    inner = json.dumps({
        "ok": True,
        "data": {
            "id": doc_id,
            "book_id": book_id,
            "type": doc_type,
            "title": title,
            "slug": "test-doc",
        },
    })
    return {
        "success": True,
        "data": {"content": inner},
    }


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------

@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_success(mock_requests):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = _mcp_ok_response()
    resp.text = json.dumps(_mcp_ok_response())
    mock_requests.post.return_value = resp

    resolver = _make_resolver()
    result = resolve_yuque_url(
        "https://yuque.antfin.com/test/doc",
        bot_id="bot1",
        user_id="user1",
        resolver=resolver,
    )

    assert result["doc_id"] == 12345
    assert result["book_id"] == 6789
    assert result["type"] == "Doc"
    assert result["title"] == "Test Doc"


# ---------------------------------------------------------------------------
# Error: missing bot_id / user_id
# ---------------------------------------------------------------------------

def test_resolve_missing_params():
    resolver = _make_resolver()
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_FAILED


# ---------------------------------------------------------------------------
# Error: no binding_id — resolver 抛 DeviceNotBoundError,_get_engine_url 翻译为 NO_BINDING
# ---------------------------------------------------------------------------

def test_resolve_no_binding():
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = DeviceNotBoundError(
        "no active binding for bot=bot1"
    )
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_NO_BINDING
    assert "未绑定设备" in exc_info.value.message


# ---------------------------------------------------------------------------
# Error: engine connection failure
# ---------------------------------------------------------------------------

@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_engine_connection_error(mock_requests):
    import requests
    mock_requests.post.side_effect = requests.exceptions.ConnectionError("refused")
    mock_requests.exceptions = requests.exceptions

    resolver = _make_resolver()
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_ENGINE_UNAVAILABLE
    assert "engine:20003" not in exc_info.value.message


# ---------------------------------------------------------------------------
# Error: engine returns non-200
# ---------------------------------------------------------------------------

@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_engine_non_200(mock_requests):
    resp = MagicMock()
    resp.status_code = 500
    resp.json.return_value = {"detail": "Internal Server Error"}
    resp.text = "Internal Server Error"
    mock_requests.post.return_value = resp

    resolver = _make_resolver()
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_ENGINE_UNAVAILABLE
    assert "engine" not in exc_info.value.message.lower() or "引擎" in exc_info.value.message


# ---------------------------------------------------------------------------
# Error: MCP tool returns not ok
# ---------------------------------------------------------------------------

@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_mcp_not_ok(mock_requests):
    inner = json.dumps({"ok": False, "error": "无权限访问该文档"})
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "data": {"content": inner}}
    resp.text = json.dumps({"success": True, "data": {"content": inner}})
    mock_requests.post.return_value = resp

    resolver = _make_resolver()
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_INVALID_URL
    assert "无权限" in exc_info.value.message


# ---------------------------------------------------------------------------
# Error: missing doc_id in resolved data
# ---------------------------------------------------------------------------

@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_missing_doc_id(mock_requests):
    inner = json.dumps({"ok": True, "data": {"type": "Doc", "title": "No ID"}})
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "data": {"content": inner}}
    resp.text = json.dumps({"success": True, "data": {"content": inner}})
    mock_requests.post.return_value = resp

    resolver = _make_resolver()
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_INVALID_URL
    assert "文档信息" in exc_info.value.message


# ---------------------------------------------------------------------------
# Error: JSON parse failure
# ---------------------------------------------------------------------------

@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_json_parse_error(mock_requests):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "data": {"content": "not-json{{"}}
    resp.text = '{"success":true,"data":{"content":"not-json{{"}}'
    mock_requests.post.return_value = resp

    resolver = _make_resolver()
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_PARSE_ERROR
    assert "engine" not in exc_info.value.message.lower() or "引擎" in exc_info.value.message


# ---------------------------------------------------------------------------
# Error: engine connection setup failure — resolver 抛非 DeviceNotBoundError 异常
# ---------------------------------------------------------------------------

def test_resolve_device_connection_failure():
    resolver = MagicMock()
    resolver.resolve_for_bot.side_effect = Exception("MOSN timeout")

    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test",
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.error_code == YUQUE_RESOLVE_ENGINE_UNAVAILABLE
    assert "MOSN" not in exc_info.value.message


# ---------------------------------------------------------------------------
# Verify: url is preserved in exception
# ---------------------------------------------------------------------------

@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_error_preserves_url(mock_requests):
    resp = MagicMock()
    resp.status_code = 500
    resp.json.return_value = {"detail": "error"}
    resp.text = "error"
    mock_requests.post.return_value = resp

    resolver = _make_resolver()
    test_url = "https://yuque.antfin.com/specific/doc"
    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            test_url,
            bot_id="bot1",
            user_id="user1",
            resolver=resolver,
        )
    assert exc_info.value.url == test_url
