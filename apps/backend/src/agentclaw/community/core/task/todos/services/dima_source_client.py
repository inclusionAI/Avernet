"""Todo-owned Dima MCP transport client."""

from __future__ import annotations

import json
from typing import Annotated, Any, Mapping, Protocol

from injector import inject

from agentclaw.community.core.task.todos.errors import (
    TodoSourceAuthError,
    TodoSourceUnavailableError,
)
from agentclaw.community.core.task.todos.source_client_protocol import (
    TodoSourceClientProtocol,
)
from agentclaw.community.plugin_api.auth import AuthRequestContext
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_GENERAL,
    HttpClient,
)
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin

_DIMA_SERVER_CODE = "mcp.ant.arkai.dimamcpserver"
_ALLOWED_NETWORK_TYPES = frozenset({"INTERNET", "OFFICE"})
_STREAMABLE_HTTP = "STREAMABLE_HTTP"


def _gateway_headers(token: str, referer: str | None = None) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "x-mcpgw-agentid": "agentid",
        "Cookie": f"IAM_TOKEN={token}",
        "x-buc-token": token,
        "Authorization": token,
        "Referer": referer or "",
        "sofa-ai-mcp": "sofa-ai-mcp:1.0",
    }


class _HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]
    content: bytes

    def json(self) -> object: ...


class DimaSourceClient(TodoSourceClientProtocol):
    """Call Dima tools without expanding the shared MCP Service API."""

    @inject
    def __init__(
        self,
        center: MCPCenterPlugin,
        http: Annotated[HttpClient, QUALIFIER_GENERAL],
    ) -> None:
        self._center = center
        self._http = http

    def call(
        self,
        *,
        actor_id: str,
        auth_request: AuthRequestContext,
        tool_name: str,
        arguments: dict[str, object],
    ) -> object:
        token = self._iam_token(actor_id, auth_request)
        endpoint = self._endpoint()
        headers = _gateway_headers(token, auth_request.base_url)

        initialize = self._post(
            endpoint,
            headers=headers,
            payload={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "agentclaw", "version": "1.0.0"},
                },
                "id": "agentclaw-todo-source-init",
            },
            timeout=15.0,
        )
        session_id = _session_id(initialize)
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        try:
            self._http.post(
                endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "method": "notifications/initialized",
                    "params": {},
                },
                timeout=5.0,
            )
        except Exception:  # noqa: BLE001 -- notification is fire-and-forget
            pass

        response = self._post(
            endpoint,
            headers=headers,
            payload={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
                "id": "agentclaw-todo-source-call",
            },
            timeout=30.0,
        )
        envelope = _parse_response(response)
        if envelope is None or envelope.get("error") is not None:
            raise TodoSourceUnavailableError("Dima MCP call failed")
        if "result" not in envelope or envelope["result"] is None:
            raise TodoSourceUnavailableError("Dima MCP call failed")
        return envelope["result"]

    def _iam_token(
        self,
        actor_id: str,
        auth_request: AuthRequestContext,
    ) -> str:
        if auth_request.verified_actor_user_id not in (None, actor_id):
            raise TodoSourceAuthError("verified actor mismatch")
        iam_token = auth_request.cookies.get("IAM_TOKEN") or auth_request.cookies.get(
            "iam_token"
        )
        if not iam_token:
            raise TodoSourceAuthError("IAM token is unavailable")
        return iam_token

    def _endpoint(self) -> str:
        try:
            detail = self._center.get_mcp_detail(_DIMA_SERVER_CODE)
        except Exception as exc:  # noqa: BLE001 -- provider failures are normalized
            raise TodoSourceUnavailableError(
                "Dima MCP metadata is unavailable"
            ) from exc
        endpoint = _select_endpoint(detail)
        if endpoint is None:
            raise TodoSourceUnavailableError("Dima MCP metadata is unavailable")
        return endpoint

    def _post(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: dict[str, object],
        timeout: float,
    ) -> _HttpResponse:
        try:
            response = self._http.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 -- transport failures are normalized
            raise TodoSourceUnavailableError("Dima MCP call failed") from exc
        if response.status_code != 200:
            raise TodoSourceUnavailableError("Dima MCP call failed")
        return response


def _select_endpoint(detail: object) -> str | None:
    if not isinstance(detail, dict):
        return None
    raw_endpoints = detail.get("endpoints", [])
    if isinstance(raw_endpoints, str):
        try:
            raw_endpoints = json.loads(raw_endpoints)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_endpoints, list):
        return None
    for endpoint in raw_endpoints:
        if not isinstance(endpoint, dict):
            continue
        if endpoint.get("networkType") not in _ALLOWED_NETWORK_TYPES:
            continue
        if endpoint.get("transportProtocol") != _STREAMABLE_HTTP:
            continue
        url = endpoint.get("url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def _session_id(response: _HttpResponse) -> str | None:
    header = _header(response.headers, "Mcp-Session-Id")
    if header:
        return header
    envelope = _parse_response(response)
    if envelope is None:
        return None
    result = envelope.get("result")
    if isinstance(result, dict):
        value = result.get("sessionId") or result.get("session_id")
        if isinstance(value, str) and value:
            return value
    value = envelope.get("sessionId") or envelope.get("session_id")
    return value if isinstance(value, str) and value else None


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if key.casefold() == target:
            return value
    return None


def _parse_response(response: _HttpResponse) -> dict[str, Any] | None:
    content_type = _header(response.headers, "Content-Type") or ""
    if "text/event-stream" in content_type.casefold():
        try:
            body = response.content.decode("utf-8")
        except UnicodeDecodeError:
            return None
        payload = _first_sse_data(body)
        if payload is None:
            return None
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return None
    else:
        try:
            value = response.json()
        except Exception:  # noqa: BLE001 -- malformed provider JSON
            return None
    return value if isinstance(value, dict) else None


def _first_sse_data(body: str) -> str | None:
    parts: list[str] = []
    in_data = False
    for line in body.splitlines():
        if line.startswith("data:"):
            parts = [line[5:]]
            in_data = True
        elif (
            in_data
            and line
            and not line.startswith(("event:", "data:", "id:", "retry:"))
        ):
            parts.append(line)
        elif in_data and not line:
            break
    return "".join(parts) if parts else None
