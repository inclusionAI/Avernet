"""MCP live fetcher — fetch fresh tools/list directly from MCP server endpoint."""
from __future__ import annotations

import json
from typing import Any

import requests

from agentclaw.community.log import get_logger

logger = get_logger()


def fetch_tools_from_endpoint(endpoint_url: str, access_token: str | None = None) -> list[dict] | None:
    """Fetch latest tools/list from an MCP server via HTTP JSON-RPC.

    Implements the full STREAMABLE_HTTP lifecycle:
      1. POST initialize to establish a session
      2. Read ``Mcp-Session-Id`` from response headers
      3. POST ``notifications/initialized``
      4. POST ``tools/list`` with the session ID

    Args:
        endpoint_url: MCP server endpoint URL (e.g. from MCP Center endpoints).
        access_token: Optional access token for the Authorization header.

    Returns:
        List of tool dicts if successful, ``None`` if the call failed.
    """
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    # ------------------------------------------------------------------
    # Step 1 — initialize session
    # ------------------------------------------------------------------
    init_payload = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "agentclaw", "version": "1.0.0"},
        },
        "id": "agentclaw-init",
    }

    try:
        resp = requests.post(endpoint_url, headers=headers, json=init_payload, timeout=15)
    except requests.exceptions.RequestException as e:
        logger.warning("[McpLiveFetcher] Initialize request failed for %s: %s", endpoint_url, e)
        return None

    if resp.status_code != 200:
        logger.warning(
            "[McpLiveFetcher] Initialize HTTP %s from %s", resp.status_code, endpoint_url
        )
        return None

    # STREAMABLE_HTTP returns the session id in a response header
    session_id = resp.headers.get("Mcp-Session-Id")
    if not session_id:
        # Some gateways return it inside the response body; try that as fallback
        body = _parse_response(resp)
        if body:
            session_id = (
                body.get("result", {}).get("sessionId")
                or body.get("result", {}).get("session_id")
                or body.get("sessionId")
                or body.get("session_id")
            )

    if not session_id:
        logger.warning(
            "[McpLiveFetcher] No sessionId returned from %s (headers=%s)",
            endpoint_url,
            dict(resp.headers),
        )
        # Continue anyway — some simple endpoints don't require a session

    if session_id:
        headers["Mcp-Session-Id"] = session_id

    # ------------------------------------------------------------------
    # Step 2 — send initialized notification
    # ------------------------------------------------------------------
    notify_payload = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    try:
        requests.post(endpoint_url, headers=headers, json=notify_payload, timeout=5)
    except requests.exceptions.RequestException:
        pass  # notification is fire-and-forget

    # ------------------------------------------------------------------
    # Step 3 — fetch tools
    # ------------------------------------------------------------------
    tools_payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": "agentclaw-live-fetch",
    }

    try:
        resp = requests.post(endpoint_url, headers=headers, json=tools_payload, timeout=15)
    except requests.exceptions.RequestException as e:
        logger.warning(
            "[McpLiveFetcher] tools/list request failed for %s: %s", endpoint_url, e
        )
        return None

    if resp.status_code != 200:
        logger.warning(
            "[McpLiveFetcher] tools/list HTTP %s from %s", resp.status_code, endpoint_url
        )
        return None

    data = _parse_response(resp)
    if data is None:
        logger.warning(
            "[McpLiveFetcher] Failed to parse response from %s", endpoint_url
        )
        return None

    if data.get("error"):
        logger.warning(
            "[McpLiveFetcher] JSON-RPC error from %s: %s",
            endpoint_url,
            data["error"],
        )
        return None

    tools = data.get("result", {}).get("tools", [])
    if not isinstance(tools, list):
        logger.warning(
            "[McpLiveFetcher] Unexpected tools type from %s: %s",
            endpoint_url,
            type(tools),
        )
        return None

    logger.info(
        "[McpLiveFetcher] Fetched %s tools from %s", len(tools), endpoint_url
    )
    return tools


def _parse_response(resp: requests.Response) -> dict | None:
    """Parse MCP server response — may be direct JSON or SSE format.

    STREAMABLE_HTTP endpoints return ``text/event-stream``.  The gateway
    may split a large JSON payload across multiple lines where only the
    first line carries the ``data:`` prefix and continuation lines have
    no prefix.  We collect the whole block and concatenate without adding
    extra newlines (the gateway already inserts them inside the payload).
    """
    content_type = resp.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        # Gateways often omit charset on text/event-stream, which causes
        # ``requests`` to guess the encoding (and frequently get it wrong
        # for CJK text).  Force UTF-8 — the JSON payload is always UTF-8.
        try:
            body = resp.content.decode("utf-8")
        except UnicodeDecodeError as e:
            logger.warning("[McpLiveFetcher] Failed to decode SSE as UTF-8: %s", e)
            return None

        lines = body.splitlines()
        data_parts: list[str] = []
        in_data = False
        for line in lines:
            if line.startswith("data:"):
                data_parts = [line[5:]]
                in_data = True
            elif in_data and line and not line.startswith(("event:", "data:", "id:", "retry:")):
                data_parts.append(line)
            elif in_data and not line:
                in_data = False
                break

        if data_parts:
            payload = "".join(data_parts)
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                logger.warning(
                    "[McpLiveFetcher] Invalid JSON in SSE payload (len=%s): %s",
                    len(payload),
                    payload[:200],
                )
                return None

        logger.warning("[McpLiveFetcher] No data line found in SSE response")
        return None

    try:
        return resp.json()
    except Exception as e:
        logger.warning("[McpLiveFetcher] Invalid JSON response: %s", e)
        return None


def _pick_best_endpoint(endpoints: list[dict[str, Any]]) -> str | None:
    """Pick the best endpoint URL for live fetching.

    Priority:
    1. STREAMABLE_HTTP + OFFICE/INTERNET
    2. SSE + OFFICE/INTERNET
    3. Any with OFFICE/INTERNET
    """
    candidates: list[tuple[int, str]] = []

    for ep in endpoints:
        network_type = ep.get("networkType", "")
        if network_type not in ("INTERNET", "OFFICE"):
            continue

        url = ep.get("url")
        if not url:
            continue

        protocol = ep.get("transportProtocol", "SSE")
        # SSE endpoints require full SSE session lifecycle which is more
        # complex than plain HTTP JSON-RPC.  Prefer STREAMABLE_HTTP.
        priority = 0 if protocol == "STREAMABLE_HTTP" else 1
        candidates.append((priority, url))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def fetch_tools_live(
    center_data: dict[str, Any],
    access_token: str | None = None,
) -> list[dict] | None:
    """Try to fetch fresh tools from an MCP server using its endpoint URLs.

    Args:
        center_data: MCP detail dict from MCP Center (must contain ``endpoints``).
        access_token: Optional access token.

    Returns:
        Fresh tools list, or ``None`` if no suitable endpoint or fetch failed.
    """
    raw_endpoints = center_data.get("endpoints", [])
    if isinstance(raw_endpoints, str):
        try:
            endpoints: list[dict[str, Any]] = json.loads(raw_endpoints)
        except json.JSONDecodeError:
            return None
    elif isinstance(raw_endpoints, list):
        endpoints = raw_endpoints
    else:
        return None

    if not endpoints:
        return None

    endpoint_url = _pick_best_endpoint(endpoints)
    if not endpoint_url:
        logger.info(
            "[McpLiveFetcher] No suitable endpoint for %s",
            center_data.get("serverCode"),
        )
        return None

    return fetch_tools_from_endpoint(endpoint_url, access_token)
