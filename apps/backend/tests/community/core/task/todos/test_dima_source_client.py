from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from agentclaw.community.core.task.todos.errors import (
    TodoSourceAuthError,
    TodoSourceUnavailableError,
)
from agentclaw.community.core.task.todos.services.dima_source_client import (
    DimaSourceClient,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext

_ENDPOINT = "https://dima-mcp.example/mcp"


@dataclass
class _Center:
    detail: object = field(
        default_factory=lambda: {
            "serverCode": "mcp.ant.arkai.dimamcpserver",
            "endpoints": [
                {
                    "networkType": "OFFICE",
                    "transportProtocol": "STREAMABLE_HTTP",
                    "url": _ENDPOINT,
                }
            ],
        }
    )
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def get_mcp_detail(self, server_code: str) -> object:
        self.calls.append(server_code)
        if self.error:
            raise self.error
        return self.detail


@dataclass
class _Http:
    outcomes: list[httpx.Response | Exception]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        self.calls.append(
            {
                "path": path,
                **kwargs,
                "headers": dict(kwargs.get("headers") or {}),
                "json": kwargs.get("json"),
            }
        )
        if not self.outcomes:
            raise AssertionError("unexpected HTTP call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _request(
    *,
    actor_id: str | None = "149844",
    cookies: dict[str, str] | None = None,
) -> AuthRequestContext:
    return AuthRequestContext(
        cookies={"IAM_TOKEN": "iam-cookie"} if cookies is None else cookies,
        verified_actor_user_id=actor_id,
    )


def _init_response(
    *,
    session_id: str | None = "session-1",
    body_session_id: str | None = None,
) -> httpx.Response:
    headers = {"Mcp-Session-Id": session_id} if session_id else {}
    result: dict[str, object] = {}
    if body_session_id:
        result["sessionId"] = body_session_id
    return httpx.Response(
        200,
        headers=headers,
        json={"jsonrpc": "2.0", "result": result},
    )


def _notification_response() -> httpx.Response:
    return httpx.Response(202, json={})


def _tool_response(result: object) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": result})


def _client(
    outcomes: list[httpx.Response | Exception],
    *,
    center: _Center | None = None,
) -> tuple[DimaSourceClient, _Center, _Http]:
    center = center or _Center()
    http = _Http(outcomes)
    return DimaSourceClient(center, http), center, http


def test_calls_dima_tool_over_streamable_http_lifecycle() -> None:
    expected = {"content": [{"type": "text", "text": '{"data": []}'}]}
    client, center, http = _client(
        [_init_response(), _notification_response(), _tool_response(expected)]
    )

    result = client.call(
        actor_id="149844",
        auth_request=_request(),
        tool_name="queryUserWorkItemList",
        arguments={"projectId": "P00000000001"},
    )

    assert result == expected
    assert center.calls == ["mcp.ant.arkai.dimamcpserver"]
    assert [call["path"] for call in http.calls] == [_ENDPOINT] * 3
    assert [call["json"]["method"] for call in http.calls] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert http.calls[0]["headers"]["Authorization"] == "iam-cookie"
    assert http.calls[0]["headers"]["Cookie"] == "IAM_TOKEN=iam-cookie"
    assert http.calls[0]["headers"]["x-buc-token"] == "iam-cookie"
    assert http.calls[0]["headers"]["x-mcpgw-agentid"] == "agentid"
    assert http.calls[0]["headers"]["Referer"] == "https://yuyan.antfin-inc.com/console"
    assert http.calls[0]["headers"]["sofa-ai-mcp"] == "sofa-ai-mcp:1.0"
    assert "Mcp-Session-Id" not in http.calls[0]["headers"]
    assert http.calls[1]["headers"]["Mcp-Session-Id"] == "session-1"
    assert http.calls[2]["headers"]["Mcp-Session-Id"] == "session-1"
    assert http.calls[2]["json"]["params"] == {
        "name": "queryUserWorkItemList",
        "arguments": {"projectId": "P00000000001"},
    }
    assert [call["timeout"] for call in http.calls] == [15.0, 5.0, 30.0]


def test_reads_session_from_initialize_body_and_parses_sse_tool_result() -> None:
    payload = {"content": [{"type": "text", "text": '{"data": [1]}'}]}
    sse = httpx.Response(
        200,
        headers={"Content-Type": "text/event-stream"},
        content=(
            b"event: message\n"
            b'data: {"jsonrpc":"2.0","result":{"content":'
            b'[{"type":"text","text":"{\\"data\\": [1]}"}]}}\n\n'
        ),
    )
    client, _, http = _client(
        [
            _init_response(session_id=None, body_session_id="body-session"),
            _notification_response(),
            sse,
        ]
    )

    result = client.call(
        actor_id="149844",
        auth_request=_request(cookies={"iam_token": "lower-cookie"}),
        tool_name="queryUserSpaceAndProject",
        arguments={},
    )

    assert result == payload
    assert http.calls[1]["headers"]["Mcp-Session-Id"] == "body-session"
    assert http.calls[2]["headers"]["Mcp-Session-Id"] == "body-session"


@pytest.mark.parametrize(
    "auth_request",
    [_request(cookies={}), _request(actor_id="another-user")],
)
def test_rejects_unusable_human_identity(
    auth_request: AuthRequestContext,
) -> None:
    client, _, http = _client([])

    with pytest.raises(TodoSourceAuthError):
        client.call(
            actor_id="149844",
            auth_request=auth_request,
            tool_name="queryUserSpaceAndProject",
            arguments={},
        )

    assert http.calls == []


@pytest.mark.parametrize(
    "detail",
    [
        None,
        {},
        {"endpoints": "not-json"},
        {"endpoints": {}},
        {
            "endpoints": [
                {
                    "networkType": "INTRANET",
                    "transportProtocol": "STREAMABLE_HTTP",
                    "url": _ENDPOINT,
                }
            ]
        },
        {
            "endpoints": [
                {
                    "networkType": "OFFICE",
                    "transportProtocol": "SSE",
                    "url": _ENDPOINT,
                }
            ]
        },
        {
            "endpoints": [
                {
                    "networkType": "OFFICE",
                    "transportProtocol": "STREAMABLE_HTTP",
                    "url": "",
                }
            ]
        },
    ],
)
def test_rejects_missing_or_unsupported_mcp_metadata(detail: object) -> None:
    client, _, http = _client([], center=_Center(detail=detail))

    with pytest.raises(TodoSourceUnavailableError, match="metadata is unavailable"):
        client.call(
            actor_id="149844",
            auth_request=_request(),
            tool_name="queryUserSpaceAndProject",
            arguments={},
        )

    assert http.calls == []


def test_normalizes_mcp_metadata_provider_failure() -> None:
    client, _, http = _client(
        [],
        center=_Center(error=RuntimeError("metadata failed")),
    )

    with pytest.raises(TodoSourceUnavailableError, match="metadata is unavailable"):
        client.call(
            actor_id="149844",
            auth_request=_request(),
            tool_name="queryUserSpaceAndProject",
            arguments={},
        )

    assert http.calls == []


@pytest.mark.parametrize(
    "initialize",
    [RuntimeError("network failed"), httpx.Response(503, json={})],
)
def test_normalizes_initialize_failures(
    initialize: httpx.Response | Exception,
) -> None:
    client, _, _ = _client([initialize])

    with pytest.raises(TodoSourceUnavailableError, match="Dima MCP call failed"):
        client.call(
            actor_id="149844",
            auth_request=_request(),
            tool_name="queryUserSpaceAndProject",
            arguments={},
        )


@pytest.mark.parametrize(
    "tool_response",
    [
        httpx.Response(500, json={}),
        httpx.Response(200, content=b"not-json"),
        httpx.Response(
            200,
            json={"jsonrpc": "2.0", "error": {"code": -32000}},
        ),
        httpx.Response(200, json={"jsonrpc": "2.0"}),
        httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=b"event: message\n\n",
        ),
    ],
)
def test_rejects_failed_or_malformed_tool_responses(
    tool_response: httpx.Response,
) -> None:
    client, _, _ = _client([_init_response(), _notification_response(), tool_response])

    with pytest.raises(TodoSourceUnavailableError, match="Dima MCP call failed"):
        client.call(
            actor_id="149844",
            auth_request=_request(),
            tool_name="queryUserSpaceAndProject",
            arguments={},
        )


def test_initialized_notification_is_fire_and_forget() -> None:
    expected = {"content": []}
    client, _, http = _client(
        [
            _init_response(),
            RuntimeError("notification failed"),
            _tool_response(expected),
        ]
    )

    result = client.call(
        actor_id="149844",
        auth_request=_request(),
        tool_name="queryUserSpaceAndProject",
        arguments={},
    )

    assert result == expected
    assert len(http.calls) == 3
