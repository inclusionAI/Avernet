"""
API-layer dependencies for request context extraction.

Migrated from: services/openclawserver/server/dependencies.py
This is the canonical new-arch location for RequestContext.
"""
from dataclasses import dataclass
from typing import Optional
from fastapi import Request, Query, HTTPException

from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth import AuthPlugin

logger = get_logger()


@dataclass
class RequestContext:
    """Request context containing user and bot information."""
    user_id: str
    bot_id: str = "default"
    nick_name: str | None = None  # 花名


async def get_request_context(
    request: Request,
    query_bot_id: Optional[str] = Query(None, alias="bot_id", description="Bot ID (takes precedence over default_bot_id)"),
    default_bot_id: Optional[str] = Query("default", description="Default Bot ID (fallback if bot_id is not provided)"),
    auth_plugin: "AuthPlugin" = Injected(AuthPlugin),
) -> RequestContext:
    """
    Extract request context from buservice auth (production) or cookies/headers (local dev).

    Priority:
    1. Buservice authentication (login session) - 生产环境强制使用
    2. Cookie 'staff_id' (SQLite local mode only)
    3. Header 'x-user-id' (SQLite local mode only)
    4. Default to 'anonymous' (SQLite local development)

    Bot ID resolution priority:
    1. bot_id query parameter (if provided) - use alias="bot_id"
    2. default_bot_id query parameter (fallback)
    3. "default" as final fallback
    """
    # Resolve bot_id: query_bot_id takes precedence over default_bot_id
    effective_bot_id = query_bot_id or default_bot_id or "default"

    # Identity resolution delegates to the injected AuthPlugin
    # (Local impl reads cookies/headers/query; prod impl runs SSO).
    # Rule 14: this dep is mode-blind.
    from agentclaw.community.adapters.http.auth.dependencies import _build_auth_context
    ctx = _build_auth_context(request)
    try:
        user = await auth_plugin.resolve_user_from_request(ctx)
    except Exception as e:
        logger.warning(f"[get_request_context] Authentication failed: {e}")
        raise HTTPException(status_code=401, detail="Authentication required") from e

    if not user or not user.staffId:
        raise HTTPException(status_code=401, detail="Authentication required")

    nick_name = user.nickName or user.staffId
    logger.info(
        "[get_request_context] User ID: %s, bot_id: %s, nick_name: %s",
        user.staffId, effective_bot_id, nick_name,
    )
    return RequestContext(
        user_id=user.staffId, bot_id=effective_bot_id, nick_name=nick_name,
    )
