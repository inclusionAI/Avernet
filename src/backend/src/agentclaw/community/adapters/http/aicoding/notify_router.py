"""
Notify router — aggregates pending HITL interactions from notification-capable bots.

GET /api/v1/notify:
  1. List notification-capable bots with active device bindings.
  2. Parallel probe each bot's Engine /api/notify endpoint — routed by
     ``device_provider`` via ``DeviceContextResolver`` (same path chat / cron
     use), so BaaS (desktop + cloud), Arca, teclaw and local each reach their
     engine through the correct transport.
  3. Return grouped result: [{bot_id, bot_name, sandbox_id, notifications}]
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.devices.services.device_context import (
    ConnInfoBuildError,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.notify.protocol import NotifyBotLister, NotifyTarget
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
    DeviceAdapterTransport,
)

logger = get_logger()

router = APIRouter(prefix="/api/v1/notify", tags=["notify"])

_NOTIFY_API_PATH = "/api/notify"
_PROBE_TIMEOUT = 5.0
_LOCAL_ENGINE_URL = "http://127.0.0.1:20003"


class NotifyEntry(BaseModel):
    """Single pending interaction item."""
    interactionId: str
    sessionKey: str
    runId: str
    kind: str
    prompt: str | None = None
    questions: list[dict[str, Any]] | None = None
    options: list[dict[str, Any]] | None = None
    subject: dict[str, Any] | None = None
    command: str | None = None
    cwd: str | None = None
    status: str
    createdAtMs: int
    expiresAtMs: int | None = None


class BotNotifySummary(BaseModel):
    """Notifications grouped by bot, including per-bot probe status."""
    bot_id: str
    bot_name: str
    sandbox_id: str
    notifications: list[NotifyEntry]
    status: str = "ok"
    error_code: str | None = None
    error_message: str | None = None


class NotifySummaryResponse(BaseModel):
    success: bool
    data: list[BotNotifySummary]


def _summarize_engine_response(
    bot_id: str,
    bot_name: str,
    sandbox_id: str,
    result: dict[str, Any],
) -> BotNotifySummary:
    """Map an Engine ``/api/notify`` ApiResponse dict to a BotNotifySummary.

    Engine returns ``{success, data, message}``:
      - ``success=False`` carries a ``"CODE: detail"`` message (e.g. relay
        ``RELAY_UNAVAILABLE``) — split it so the wire error code surfaces in
        ``error_code`` and downstream can classify without parsing logs.
      - ``success=True`` wraps the pending interaction list under ``data``.
    """
    if not result.get("success"):
        logger.warning(f"[notify] probe failed for bot={bot_id}: {result}")
        message = str(
            result.get("message")
            or result.get("error")
            or "Engine notify failed"
        )
        code, separator, detail = message.partition(":")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
            status="error",
            error_code=code.strip() if separator else "ENGINE_NOTIFY_ERROR",
            error_message=detail.strip() if separator else message,
        )

    notifications_data = result.get("data", [])
    logger.info(
        f"[notify] bot={bot_id} got {len(notifications_data)} notifications from engine"
    )

    notifications = [NotifyEntry(**n) for n in notifications_data] if notifications_data else []

    return BotNotifySummary(
        bot_id=bot_id,
        bot_name=bot_name,
        sandbox_id=sandbox_id,
        notifications=notifications,
    )


def _http_error_summary(
    bot_id: str, bot_name: str, sandbox_id: str, status_code: int
) -> BotNotifySummary:
    return BotNotifySummary(
        bot_id=bot_id,
        bot_name=bot_name,
        sandbox_id=sandbox_id,
        notifications=[],
        status="error",
        error_code=f"ENGINE_HTTP_{status_code}",
        error_message=f"Engine notify returned HTTP {status_code}",
    )


async def _probe_local(
    bot_id: str, bot_name: str, sandbox_id: str
) -> BotNotifySummary | None:
    """Local dev probe: direct httpx to the local engine process.

    Preserves legacy local-mode behavior. Routed through the transport in
    local/singlebox boots would miss: the test/singlebox transport is a
    cron-only in-memory adapter that does not serve ``/api/notify``.
    """
    url = f"{_LOCAL_ENGINE_URL}{_NOTIFY_API_PATH}"
    logger.info(f"[notify] probing bot={bot_id} at {url} (local)")
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.warning(f"[notify] HTTP {resp.status_code} for bot={bot_id}")
            return _http_error_summary(bot_id, bot_name, sandbox_id, resp.status_code)
        result = resp.json()
        logger.info(f"[notify] bot={bot_id} response status={resp.status_code}")
        return _summarize_engine_response(bot_id, bot_name, sandbox_id, result)
    except Exception as e:
        logger.error(f"[notify] error probing bot={bot_id}: {e}")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
            status="error",
            error_code="NOTIFY_PROBE_ERROR",
            error_message=str(e),
        )


async def _probe_bot_notify(
    target: NotifyTarget,
    resolver: DeviceContextResolver,
    transport: DeviceAdapterTransport,
) -> BotNotifySummary | None:
    """Call Engine ``/api/notify`` for a single bot, routed by provider.

    Provider routing (baas / arca / teclaw / local) is resolved via
    ``DeviceContextResolver`` from ``(bot_id, owner_id)`` — the same single
    source of truth chat / cron use. Before this, every bot was probed through
    ``ArcaSandboxClient.build_proxy_request`` (Arca proxypass only), which
    produced an unroutable target ``ARCA_BOT-…:20003`` for every BaaS bot and
    surfaced as ``ENGINE_HTTP_500``.
    """
    bot_id = target.bot_id
    bot_name = target.bot_name
    sandbox_id = target.sandbox_id
    owner_id = target.owner_id

    # owner_id is the binding owner (== user for own bots; the real owner for
    # collaborator bots) so the right binding is resolved — mirrors
    # collaborator_service / session_resources callers.
    try:
        ctx = resolver.resolve_for_bot(bot_id, owner_id)
    except DeviceNotBoundError:
        logger.info(f"[notify] bot={bot_id} has no active binding, skipping probe")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
        )
    except (UnknownProviderError, ConnInfoBuildError) as e:
        logger.error(f"[notify] resolve failed bot={bot_id}: {e}")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
            status="error",
            error_code="NOTIFY_RESOLVE_ERROR",
            error_message=str(e),
        )

    logger.info(
        f"[notify] probing bot={bot_id} provider={ctx.provider} bot_type={ctx.bot_type}"
    )

    if ctx.provider == "local":
        return await _probe_local(bot_id, bot_name, sandbox_id)

    try:
        result = await transport.invoke(
            ctx.conn_info, "GET", _NOTIFY_API_PATH, timeout=_PROBE_TIMEOUT
        )
        # summarize inside the try so malformed notification payloads (e.g. a
        # NotifyEntry missing required fields) are caught and surfaced as a
        # probe error rather than escaping to the caller.
        return _summarize_engine_response(bot_id, bot_name, sandbox_id, result)
    except DeviceAdapterHTTPStatusError as e:
        logger.warning(
            f"[notify] HTTP {e.status_code} for bot={bot_id} provider={ctx.provider}"
        )
        return _http_error_summary(bot_id, bot_name, sandbox_id, e.status_code)
    except DeviceAdapterEndpointNotFoundError as e:
        logger.warning(f"[notify] endpoint not found for bot={bot_id}: {e}")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
            status="error",
            error_code="ENGINE_HTTP_404",
            error_message="Engine notify endpoint not found",
        )
    except DeviceAdapterTimeoutError as e:
        logger.warning(f"[notify] timeout for bot={bot_id}: {e}")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
            status="error",
            error_code="ENGINE_TIMEOUT",
            error_message=str(e),
        )
    except Exception as e:
        logger.error(f"[notify] error probing bot={bot_id}: {e}")
        return BotNotifySummary(
            bot_id=bot_id,
            bot_name=bot_name,
            sandbox_id=sandbox_id,
            notifications=[],
            status="error",
            error_code="NOTIFY_PROBE_ERROR",
            error_message=str(e),
        )


@router.get("", response_model=NotifySummaryResponse)
async def get_notify_summary(
    user: AuthenticatedUser = Depends(get_current_user),
    bot_lister: NotifyBotLister = Injected(NotifyBotLister),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    transport: DeviceAdapterTransport = Injected(DeviceAdapterTransport),
):
    """
    Get aggregated pending notifications from notification-capable user bots.

    Returns a list of bot summaries, each containing the pending HITL
    interactions from that bot's engine.
    """
    user_id = user.staffId
    targets = bot_lister.list_bot_mappings(user_id)

    if not targets:
        return NotifySummaryResponse(success=True, data=[])

    # Parallel probe all bots
    summaries = await asyncio.gather(*[
        _probe_bot_notify(target, resolver, transport)
        for target in targets
    ])

    # Filter out None results and empty notification lists for cleaner response
    data = [s for s in summaries if s is not None]

    return NotifySummaryResponse(success=True, data=data)
