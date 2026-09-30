"""MCP resolve helper — resolve yuque URLs via engine adapter HTTP call.

mcporter only exists inside the arca container, so the backend delegates
the actual CLI invocation to the engine adapter's ``POST /api/mcp/call-tool`` endpoint.
"""

import json
import logging
from typing import TYPE_CHECKING

import requests as sync_requests

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )

logger = logging.getLogger(__name__)

_MCP_TOOL = "mcp.ant.faas.skylarkmcpserver.skylarkmcpserver.skylark_resolve_url"


# ── Error codes ──

YUQUE_RESOLVE_NO_BINDING = "YUQUE_RESOLVE_NO_BINDING"
YUQUE_RESOLVE_ENGINE_UNAVAILABLE = "YUQUE_RESOLVE_ENGINE_UNAVAILABLE"
YUQUE_RESOLVE_FAILED = "YUQUE_RESOLVE_FAILED"
YUQUE_RESOLVE_INVALID_URL = "YUQUE_RESOLVE_INVALID_URL"
YUQUE_RESOLVE_PARSE_ERROR = "YUQUE_RESOLVE_PARSE_ERROR"


class YuqueResolveError(Exception):
    """Structured exception for yuque URL resolution failures.

    Attributes:
        message: User-facing message (safe to display in frontend).
        error_code: Machine-readable error code for frontend branching.
        url: The yuque URL that failed to resolve.
    """

    def __init__(self, message: str, error_code: str, url: str = ""):
        self.message = message
        self.error_code = error_code
        self.url = url
        super().__init__(message)


def _get_engine_url(
    bot_id: str,
    user_id: str,
    resolver: "DeviceContextResolver",
) -> dict:
    """Resolve the engine adapter base URL and headers for *bot_id*.

    走 ``DeviceContextResolver`` (全仓唯一 provider 解析点),
    替代旧的 ``bot_service.get_bot → device_service.get_device_connection_v2``。

    ``DeviceNotBoundError`` 被翻成结构化 ``YuqueResolveError(NO_BINDING)``,
    保持前端错误码契约不变。
    """
    from agentclaw.community.core.devices.services.device_context import (
        DeviceNotBoundError,
    )

    try:
        ctx = resolver.resolve_for_bot(bot_id, user_id)
    except DeviceNotBoundError as exc:
        raise YuqueResolveError(
            message="Bot 未绑定设备，无法解析语雀链接",
            error_code=YUQUE_RESOLVE_NO_BINDING,
        ) from exc
    return ctx.conn_info


def resolve_yuque_url(
    url: str,
    bot_id: str = "",
    user_id: str = "",
    *,
    resolver: "DeviceContextResolver",
) -> dict:
    """Resolve a yuque URL via the engine adapter's MCP call-tool endpoint.

    Returns dict with keys: doc_id, book_id, type (Doc/Book), title, slug
    Raises YuqueResolveError on failure (structured, frontend-safe).
    """
    if not bot_id or not user_id:
        raise YuqueResolveError(
            message="缺少必要参数，无法解析语雀链接",
            error_code=YUQUE_RESOLVE_FAILED,
            url=url,
        )

    try:
        conn = _get_engine_url(bot_id, user_id, resolver)
        engine_url = conn["url"]
        engine_headers = dict(conn.get("headers") or {})
    except YuqueResolveError:
        raise
    except Exception as exc:
        logger.error(
            "[yuque_resolve] Failed to get engine connection for bot %s: %s",
            bot_id, exc,
        )
        raise YuqueResolveError(
            message="引擎服务连接失败，请稍后重试",
            error_code=YUQUE_RESOLVE_ENGINE_UNAVAILABLE,
            url=url,
        ) from exc

    call_tool_url = f"{engine_url.rstrip('/')}/api/mcp/call-tool"
    payload = {
        "tool": _MCP_TOOL,
        "args": [f"url={url}"],
    }

    logger.info(
        "[yuque_resolve] POST %s tool=%s url=%s",
        call_tool_url, _MCP_TOOL, url,
    )

    try:
        resp = sync_requests.post(
            call_tool_url,
            json=payload,
            headers=engine_headers,
            timeout=30,
        )
    except sync_requests.exceptions.RequestException as exc:
        logger.error(
            "[yuque_resolve] Engine request failed: url=%s error=%s",
            call_tool_url, exc,
        )
        raise YuqueResolveError(
            message="引擎服务连接失败，请稍后重试",
            error_code=YUQUE_RESOLVE_ENGINE_UNAVAILABLE,
            url=url,
        ) from exc

    logger.info(
        "[yuque_resolve] Response status=%s body_preview=%s",
        resp.status_code, resp.text[:500],
    )

    if resp.status_code != 200:
        detail = ""
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        logger.error(
            "[yuque_resolve] Engine returned %s: %s", resp.status_code, detail,
        )
        raise YuqueResolveError(
            message="引擎服务返回错误，请检查 Bot 是否正常运行",
            error_code=YUQUE_RESOLVE_ENGINE_UNAVAILABLE,
            url=url,
        )

    try:
        body = resp.json()
    except json.JSONDecodeError as exc:
        logger.error(
            "[yuque_resolve] Failed to parse engine response: %s, raw=%s",
            exc, resp.text[:500],
        )
        raise YuqueResolveError(
            message="引擎返回了无法解析的数据，请联系管理员",
            error_code=YUQUE_RESOLVE_PARSE_ERROR,
            url=url,
        ) from exc

    if not body.get("success"):
        error_msg = body.get("message") or "未知错误"
        logger.error(
            "[yuque_resolve] MCP tool call not successful: %s body=%s",
            error_msg, str(body)[:500],
        )
        raise YuqueResolveError(
            message=f"语雀链接解析失败: {error_msg}",
            error_code=YUQUE_RESOLVE_FAILED,
            url=url,
        )

    content_str = (body.get("data") or {}).get("content", "")
    try:
        data = json.loads(content_str)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.error(
            "[yuque_resolve] Failed to parse MCP content as JSON: %s, raw=%s",
            exc, content_str[:500],
        )
        raise YuqueResolveError(
            message="引擎返回了无法解析的数据，请联系管理员",
            error_code=YUQUE_RESOLVE_PARSE_ERROR,
            url=url,
        ) from exc

    if not isinstance(data, dict) or not data.get("ok"):
        mcp_error = data.get("error", "") if isinstance(data, dict) else str(data)
        logger.error(
            "[yuque_resolve] skylark_resolve_url returned not ok: %s", mcp_error,
        )
        raise YuqueResolveError(
            message=f"语雀链接解析失败: {mcp_error}" if mcp_error else "语雀链接无效或无权访问",
            error_code=YUQUE_RESOLVE_INVALID_URL,
            url=url,
        )

    resolved = data.get("data", {})
    doc_type = resolved.get("type", "Doc")
    doc_id = resolved.get("id")
    book_id = resolved.get("book_id")

    if not doc_id:
        logger.error(
            "[yuque_resolve] Missing doc_id in resolved data: url=%s data=%s",
            url, resolved,
        )
        raise YuqueResolveError(
            message="语雀链接解析失败：无法获取文档信息，请检查链接是否有效",
            error_code=YUQUE_RESOLVE_INVALID_URL,
            url=url,
        )

    result_dict = {
        "doc_id": doc_id,
        "book_id": book_id,
        "type": doc_type,
        "title": resolved.get("title", ""),
        "slug": resolved.get("slug", ""),
    }
    logger.info(
        "[yuque_resolve] Resolved url=%s -> doc_id=%s, book_id=%s, type=%s",
        url, doc_id, book_id, doc_type,
    )
    return result_dict
