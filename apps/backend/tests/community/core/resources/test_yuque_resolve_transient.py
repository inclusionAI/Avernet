"""Transient retry behavior for Yuque link resolution.

Covers the ``mcporter`` streamable HTTP transient failures observed from
engine endpoint when the tool call returns HTTP 200 but no ``success`` payload.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.devices.services.device_context import DeviceContext
from agentclaw.community.core.resources.yuque_resolve import (
    _is_transient_streamable_http,
    YUQUE_RESOLVE_ENGINE_UNAVAILABLE,
    YUQUE_RESOLVE_FAILED,
    YUQUE_RESOLVE_PARSE_ERROR,
    YuqueResolveError,
    resolve_yuque_url,
)

_TRANSIENT_CONTENT = "[mcporter] Streamable HTTP error: Error POSTing to endpoint:"

_TOOL = "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_resolve_url"


def _make_resolver(conn_url="http://engine:20003") -> MagicMock:
    resolver = MagicMock()
    resolver.resolve_for_bot.return_value = DeviceContext(
        provider="baas",
        conn_info={
            "url": conn_url,
            "headers": {"X-Token": "test"},
            "binding_id": 42,
        },
        binding_id=42,
        bot_id="bot1",
        user_id="user1",
    )
    return resolver


def _response(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _failure_response(content: str = _TRANSIENT_CONTENT) -> dict:
    return {
        "success": False,
        "data": {
            "tool_name": _TOOL,
            "server_code": "",
            "content": content,
            "is_error": True,
        },
        "message": None,
        "warning": None,
        "total": None,
        "error": None,
    }


def _ok_response(doc_id=123, book_id=456) -> dict:
    inner = json.dumps(
        {
            "ok": True,
            "data": {
                "id": doc_id,
                "book_id": book_id,
                "type": "Doc",
                "title": "test-doc",
                "slug": "test-doc",
            },
        }
    )
    return {"success": True, "data": {"content": inner}}


@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_retry_on_streamable_http_transient_failure(mock_requests):
    first = _response(_failure_response())
    second = _response(_ok_response())
    mock_requests.post.side_effect = [first, second]

    result = resolve_yuque_url(
        "https://yuque.antfin.com/test/doc",
        bot_id="bot1",
        user_id="user1",
        resolver=_make_resolver(),
    )

    assert mock_requests.post.call_count == 2
    assert result == {
        "doc_id": 123,
        "book_id": 456,
        "type": "Doc",
        "title": "test-doc",
        "slug": "test-doc",
    }


@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_transient_detector_ignores_unexpected_payload_shapes(mock_requests):
    first = _response(_failure_response())
    second = _response(_ok_response())
    mock_requests.post.side_effect = [first, second]

    assert not _is_transient_streamable_http({"success": True})
    assert not _is_transient_streamable_http({"success": False, "data": None})
    result = resolve_yuque_url(
        "https://yuque.antfin.com/test/doc",
        bot_id="bot1",
        user_id="user1",
        resolver=_make_resolver(),
    )
    assert mock_requests.post.call_count == 2
    assert result["doc_id"] == 123


@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_non_200_response_with_malformed_body_is_engine_unavailable(mock_requests):
    resp = MagicMock()
    resp.status_code = 502
    resp.json.side_effect = ValueError("invalid body")
    resp.text = "upstream not available"
    mock_requests.post.return_value = resp

    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test/doc",
            bot_id="bot1",
            user_id="user1",
            resolver=_make_resolver(),
        )

    assert mock_requests.post.call_count == 1
    assert exc_info.value.error_code == YUQUE_RESOLVE_ENGINE_UNAVAILABLE
    assert exc_info.value.url == "https://yuque.antfin.com/test/doc"


@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_malformed_engine_body_is_parse_error(mock_requests):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = json.JSONDecodeError("invalid body", "not-json", 0)
    resp.text = "not-json"
    mock_requests.post.return_value = resp

    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test/doc",
            bot_id="bot1",
            user_id="user1",
            resolver=_make_resolver(),
        )

    assert mock_requests.post.call_count == 1
    assert exc_info.value.error_code == YUQUE_RESOLVE_PARSE_ERROR
    assert exc_info.value.url == "https://yuque.antfin.com/test/doc"


@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_non_transient_tool_failure_is_not_retried(mock_requests):
    body = _failure_response(content="not a transient failure")
    body["message"] = "tool did not accept this url"
    mock_requests.post.return_value = _response(body)

    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test/doc",
            bot_id="bot1",
            user_id="user1",
            resolver=_make_resolver(),
        )

    assert mock_requests.post.call_count == 1
    assert exc_info.value.error_code == YUQUE_RESOLVE_FAILED
    assert "tool did not accept this url" in exc_info.value.message


@patch("agentclaw.community.core.resources.yuque_resolve.sync_requests")
def test_resolve_reports_engine_unavailable_after_two_transient_failures(mock_requests):
    mock_requests.post.side_effect = [_response(_failure_response()), _response(_failure_response())]

    with pytest.raises(YuqueResolveError) as exc_info:
        resolve_yuque_url(
            "https://yuque.antfin.com/test/doc",
            bot_id="bot1",
            user_id="user1",
            resolver=_make_resolver(),
        )

    assert mock_requests.post.call_count == 2
    assert exc_info.value.error_code == YUQUE_RESOLVE_ENGINE_UNAVAILABLE
    assert "引擎服务暂时不可用" in exc_info.value.message
