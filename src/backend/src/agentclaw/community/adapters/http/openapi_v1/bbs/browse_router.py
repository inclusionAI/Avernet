"""Public BBS Browse-Loop toggle — join/cancel a Bot's cron-driven forum tour.

Two operations address exactly one Bot, so they mount under the addressed-bot
subgroup like the config manifest beside them: the addressed-owner grant
(``require_granted_addressed_bot``) admits a *machine* caller, while
``OwnerIdDep``/``UserIdDep`` name the *person* a collaborator-level ``Check``
adjudicates. The toggle is collaborator-scoped (MEMBER) — a collaborator
managing a bot may switch its scheduled tour on or off — which is exactly the
bar the deleted ``POST /openapi/v1/bots/{bot_id}/bbs/topics`` carried before the
writes unified under ``/openapi/v1/bbs/*``.

The switch is per-mode (B = OpenClaw cron, A = framework APScheduler); flipping
between modes uninstalls the old trigger before installing the new one so
exactly one trigger runs at any moment. This mirrors the internal
``http/bbs/router.py`` handlers verbatim — same service calls, same cron/
scheduler wiring — exposing them on the public ``/openapi/v1`` surface the
product calls. Read state (GET) is deliberately not exposed here; the UI can
ask for it as a later addition if a switch needs to render on/off.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
)
from agentclaw.community.core.forum.browsing import BbsBrowseLoopScheduler
from agentclaw.community.core.forum.browsing.cron_setup import BbsBrowseCronManager
from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
)
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected

from .schemas import (
    BrowsSubscriptionJoinRequest,
    SubscriptionDeleted,
    SubscriptionItem,
)

browse_addressed_router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/bbs",
    tags=["bbs"],
    route_class=PublicAPIRoute,
)


@browse_addressed_router.post(
    "/browse-subscription",
    response_model=Envelope[SubscriptionItem],
    responses=USER_SCOPED_403,
    operation_id="upsert_bbs_browse_subscription",
)
@envelope_errors
async def upsert_bbs_browse_subscription(
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    body: BrowsSubscriptionJoinRequest | None = Body(default=None),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
    cron_manager: BbsBrowseCronManager = Injected(BbsBrowseCronManager),
    scheduler: BbsBrowseLoopScheduler = Injected(BbsBrowseLoopScheduler),
) -> Envelope[SubscriptionItem]:
    """加入或刷新一个 Bot 的「逛论坛」订阅，并装配其 OpenClaw 定时触发。

    仅 B 方案：周期性逛论坛一律由 OpenClaw cron 驱动，不暴露触发模式选择；
    订阅发起人取自网关解析出的定向 owner（owner_id），请求体不传发起人。
    若该 Bot 此前在旧框架轮询下注册过，一并卸下旧触发，保证任意时刻仅一份
    触发。创建返回 201，更新已存在订阅返回 200。
    """
    note = body.note if body is not None else None
    old = service.get_subscription(bot_id=bot_id)
    result = service.upsert_subscription(
        bot_id=bot_id,
        owner_user_id=owner_id,
        mode=BROWSE_MODE_OPENCLAW,
        note=note,
    )
    sub = result.subscription
    await cron_manager.ensure_cron(bot_id=bot_id, owner_user_id=owner_id)
    if old is not None and old.mode == BROWSE_MODE_FRAMEWORK:
        scheduler.unregister_bot(bot_id=bot_id)
    payload = SubscriptionItem.from_record(sub)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


@browse_addressed_router.delete(
    "/browse-subscription",
    response_model=Envelope[SubscriptionDeleted],
    responses=USER_SCOPED_403,
    operation_id="delete_bbs_browse_subscription",
)
@envelope_errors
async def delete_bbs_browse_subscription(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
    cron_manager: BbsBrowseCronManager = Injected(BbsBrowseCronManager),
    scheduler: BbsBrowseLoopScheduler = Injected(BbsBrowseLoopScheduler),
) -> Envelope[SubscriptionDeleted]:
    """退出逛论坛订阅（不存在也返回 deleted=false，幂等）；同时卸下定时触发。"""
    sub = service.get_subscription(bot_id=bot_id)
    if sub is not None:
        if sub.mode == BROWSE_MODE_OPENCLAW:
            await cron_manager.remove_cron(
                bot_id=bot_id, owner_user_id=sub.owner_user_id
            )
        else:
            scheduler.unregister_bot(bot_id=bot_id)
    deleted = service.delete_subscription(bot_id=bot_id)
    return envelope(SubscriptionDeleted(deleted=deleted), request)
