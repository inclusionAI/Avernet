"""Public BBS Browse-Loop toggle + reads + manual triggers on the openapi surface.

The join/cancel writes and the per-Bot reads/feed/triggers all share the
`/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs` prefix. The whole group is mounted in
`_OPEN_SUBGROUPS` (only `require_principal` + base `ERROR_RESPONSES`):
the body declares the operator, the path names the addressed Bot, and nothing
on this group is owner-scoped. This mirrors the internal `http/bbs/router.py`
handlers verbatim — same service / cron / scheduler calls — exposing them on
the public `/openapi/v1` surface the product and app-to-app callers reach.

Beyond the toggle writes, the group also serves the single-Bot subscription
read, the actor-aware feed the Browse Loop consumes each tick, and four
manual triggers used to independently verify the A (framework cron) and B
(OpenClaw self-cron) delivery paths without waiting for the timer.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.task.forum.browsing import (
    BbsBrowseLoopRunner,
    BbsBrowseLoopScheduler,
)
from agentclaw.community.core.task.forum.browsing.cron_setup import (
    BbsBrowseCronManager,
    BbsDigestCronManager,
)
from agentclaw.community.core.task.forum.browsing.subscription_provisioner import (
    BbsBrowseSubscriptionProvisioner,
)
from agentclaw.community.core.task.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
)
from agentclaw.community.core.task.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected

from .schemas import (
    BrowseFeedTopicItem,
    BrowsSubscriptionJoinRequest,
    SubscriptionDeleted,
    SubscriptionItem,
)

browse_open_router = APIRouter(
    prefix="/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs",
    tags=["bbs"],
    route_class=PublicAPIRoute,
)


@browse_open_router.post(
    "/browse-subscription",
    response_model=Envelope[SubscriptionItem],
    operation_id="upsert_bbs_browse_subscription",
    dependencies=[Depends(require_principal)],
    responses={
        202: {
            "model": Envelope[SubscriptionItem],
            "description": "新订阅已接受，异步装配成功后才持久化",
        }
    },
)
@envelope_errors
async def upsert_bbs_browse_subscription(
    bot_id: BotIdPath,
    owner_user_id: Annotated[
        str,
        Query(
            min_length=1,
            max_length=256,
            description=(
                "The owner whose Bot's Browse loop is switched on (work-no) —"
                " A backend API: the owner is declared as an explicit query "
                "parameter, never resolved from or restricted to the logged-in "
                "caller -- mirrors the bot-scoped browse-subscription "
                "handler verbatim. The value is the same identifier as "
                "SubscriptionItem.owner_user_id."
            ),
        ),
    ],
    request: Request,
    response: Response,
    body: BrowsSubscriptionJoinRequest | None = Body(default=None),
    provisioner: BbsBrowseSubscriptionProvisioner = Injected(
        BbsBrowseSubscriptionProvisioner
    ),
) -> Envelope[SubscriptionItem]:
    """加入或刷新一个 Bot 的「逛论坛」订阅（统一 B 方案）。

    新订阅先返回 202 并异步装配 Browse 与 Digest 两条 Bot 侧 cron；装配成功后才写入
    订阅记录，装配失败则不留下数据库开关。已存在的 OpenClaw 订阅同步保存备注并后台
    修复 cron。
    """
    note = body.note if body is not None else None
    result = provisioner.join_or_refresh(
        bot_id=bot_id,
        owner_user_id=owner_user_id,
        note=note,
    )
    payload = SubscriptionItem.from_record(result.subscription)
    if result.persisted:
        response.status_code = 201 if result.created else 200
        return (
            created(payload, request) if result.created else envelope(payload, request)
        )

    response.status_code = 202
    return accepted(payload, request)


@browse_open_router.delete(
    "/browse-subscription",
    response_model=Envelope[SubscriptionDeleted],
    operation_id="delete_bbs_browse_subscription",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def delete_bbs_browse_subscription(
    bot_id: BotIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
    cron_manager: BbsBrowseCronManager = Injected(BbsBrowseCronManager),
    digest_cron_manager: BbsDigestCronManager = Injected(BbsDigestCronManager),
    scheduler: BbsBrowseLoopScheduler = Injected(BbsBrowseLoopScheduler),
    provisioner: BbsBrowseSubscriptionProvisioner = Injected(
        BbsBrowseSubscriptionProvisioner
    ),
) -> Envelope[SubscriptionDeleted]:
    """退出逛论坛订阅（不存在也返回 deleted=false，幂等）；同时卸下定时触发。"""
    await provisioner.cancel_pending(bot_id=bot_id)
    sub = service.get_subscription(bot_id=bot_id)
    if sub is not None:
        if sub.mode == BROWSE_MODE_OPENCLAW:
            await cron_manager.remove_cron(
                bot_id=bot_id, owner_user_id=sub.owner_user_id
            )
            await digest_cron_manager.remove_cron(
                bot_id=bot_id, owner_user_id=sub.owner_user_id
            )
        else:
            scheduler.unregister_bot(bot_id=bot_id)
    deleted = service.delete_subscription(bot_id=bot_id)
    return envelope(SubscriptionDeleted(deleted=deleted), request)


# ---------------------------------------------------------------------------
# Public reads + manual triggers — exposed on the openapi surface so the
# product UI can render per-Bot on/off state and let a user drive a one-shot
# Browse without a separate internal ticket. These mirror the internal
# `/api/v1/collaboration/tasks/bots/{bot_id}/bbs/*` handlers verbatim (same service/runner calls),
# differing only in the authorisation seam: the public routes require a
# verified OpenAPI principal and admit OPEN / NoCheck — they name no owner on
# the wire, so there is no addressed-owner grant to check and no logged-in-user
# coupling. The declared `bot_id` path segment is the only key.
# ---------------------------------------------------------------------------


@browse_open_router.get(
    "/browse-subscription",
    response_model=Envelope[SubscriptionItem],
    operation_id="get_bbs_browse_subscription",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def get_bbs_browse_subscription(
    bot_id: BotIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[SubscriptionItem]:
    """读取该 Bot 当前的逛论坛订阅状态，不存在则 404。

    Read state lives beside the join/cancel writes so the UI can render a Bot
    on/off switch. It is a backend read keyed by the addressed `bot_id`: the
    owner is not assumed from the principal, so admission is OPEN and the
    authorisation row is NoCheck.
    """
    sub = service.get_subscription(bot_id=bot_id)
    if sub is None:
        raise NotFound("subscription not found")
    return envelope(SubscriptionItem.from_record(sub), request)


@browse_open_router.get(
    "/feed",
    response_model=Envelope[Page[BrowseFeedTopicItem]],
    operation_id="list_bbs_browse_feed",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def list_bbs_browse_feed(
    bot_id: BotIdPath,
    request: Request,
    page_params: PageParamsDep,
    status: str | None = Query(
        default=None,
        max_length=16,
        description="Optional Topic status filter: OPEN, CLOSED, or LOCKED.",
    ),
    topic_type: str | None = Query(
        default=None,
        max_length=16,
        description="Optional Topic type filter: DISCUSSION, POLL, or NOTICE.",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[BrowseFeedTopicItem]]:
    """读取该 Bot 待处理的 Topic feed（POLL/NOTICE 已回复则不再出现）。

    The Feed the Browse Loop actor consumes each tick. Exposed on the public
    surface so a collaborator may preview what a Bot will act on without filing
    an ops ticket.
    """
    result = service.list_browse_feed(
        bot_id=bot_id,
        status=status,
        topic_type=topic_type,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page_envelope(
        result.total,
        [BrowseFeedTopicItem.from_record(item) for item in result.items],
        request,
    )


@browse_open_router.post(
    "/browse-loop/trigger-framework",
    response_model=Envelope[dict[str, Any]],
    operation_id="trigger_bbs_browse_framework",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def trigger_bbs_browse_framework(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """A 模式手动触发：按框架 cron 口径向该 Bot 推送一次「立即逛论坛」。

    The addressed subscription must exist with mode=framework. Exposed on the
    public surface so the product can independently verify the A delivery path
    is wired.
    """
    result = await runner.push_browse_once(
        bot_id=bot_id, expected_mode=BROWSE_MODE_FRAMEWORK
    )
    return envelope(dict(result), request)


@browse_open_router.post(
    "/browse-loop/trigger-self",
    response_model=Envelope[dict[str, Any]],
    operation_id="trigger_bbs_browse_self",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def trigger_bbs_browse_self(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """B 模式手动触发：按 OpenClaw 自带 cron 口径向该 Bot 推送一次「立即逛论坛」。

    The addressed subscription must exist with mode=openclaw. Production is
    driven by the Bot local */30 cron; this route only verifies the Bot can
    complete one Browse Run on its own.
    """
    result = await runner.push_browse_once(
        bot_id=bot_id, expected_mode=BROWSE_MODE_OPENCLAW
    )
    return envelope(dict(result), request)


@browse_open_router.post(
    "/browse-loop/cron-register",
    response_model=Envelope[dict[str, Any]],
    operation_id="trigger_bbs_browse_cron_register",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def trigger_bbs_browse_cron_register(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """通知订阅的 Bot 注册其本地 */30 cron（B 模式装配步骤）。"""
    result = await runner.push_cron_event(bot_id=bot_id, action="register")
    return envelope(dict(result), request)


@browse_open_router.post(
    "/browse-loop/cron-remove",
    response_model=Envelope[dict[str, Any]],
    operation_id="trigger_bbs_browse_cron_remove",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def trigger_bbs_browse_cron_remove(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """通知订阅的 Bot 移除其本地 bbs-browse cron（退出/降级）。"""
    result = await runner.push_cron_event(bot_id=bot_id, action="remove")
    return envelope(dict(result), request)
