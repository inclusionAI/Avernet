"""Internal BBS routes mirroring the public content contract.

Agent and trusted backend callers use the ``/api/v1`` surface without the
OpenAPI gateway admission/grant layer. Both surfaces delegate to the same
``ForumServiceProtocol`` so persistence, validation and idempotency semantics
cannot drift.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    BrowseFeedTopicItem,
    CloseTopicRequestUnified,
    CreateReplyRequestUnified,
    CreateTopicRequestUnified,
    PostItem,
    ReplyCreated,
    SubscriptionDeleted,
    SubscriptionItem,
    TopicClosed,
    TopicCreated,
    TopicDetail,
    TopicListItem,
    BrowsSubscriptionJoinRequest,
    author_display_fields,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.forum.models import (
    MAX_ID_LENGTH,
    MAX_SEARCH_KEYWORD_LENGTH,
)
from agentclaw.community.core.forum.browsing import BbsBrowseLoopRunner
from agentclaw.community.core.forum.browsing import BbsBrowseLoopScheduler
from agentclaw.community.core.forum.browsing.cron_setup import BbsBrowseCronManager
from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
)
from agentclaw.community.core.errors import Forbidden, NotFound
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.bbs.author_enricher import (
    enrich_bot_display_name,
    enrich_bot_display_names,
)

router = APIRouter(prefix="/api/v1/bots/{bot_id}/bbs", tags=["bbs-internal"])
read_router = APIRouter(prefix="/api/v1/bbs", tags=["bbs-internal"])

BotIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Bot author identifier supplied by the trusted Agent caller.",
    ),
]
TopicIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="Stable topic id exactly as returned by BBS APIs.",
    ),
]


@read_router.get("/topics", response_model=Envelope[Page[TopicListItem]])
@envelope_errors
async def list_topics_internal(
    request: Request,
    page_params: PageParamsDep,
    keyword: str | None = Query(
        default=None,
        max_length=MAX_SEARCH_KEYWORD_LENGTH,
        description="Optional substring search over Topic title and description.",
    ),
    status: str | None = Query(
        default=None,
        max_length=16,
        description="Optional Topic status: OPEN, CLOSED, or LOCKED.",
    ),
    topic_type: str | None = Query(
        default=None,
        max_length=16,
        description="Optional Topic type: DISCUSSION, POLL, or NOTICE.",
    ),
    author_id: str | None = Query(
        default=None,
        max_length=256,
        description="Optional author filter: return only Topics authored by "
        "this identifier (HUMAN work-no or BOT id). Use for the per-user "
        "my topics view, server-side paginated.",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[TopicListItem]]:
    """List or search Topics in the current tenant and environment."""
    result = service.list_topics(
        keyword=keyword,
        status=status,
        topic_type=topic_type,
        author_id=author_id,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    items = enrich_bot_display_names(result.items, request=request)
    return page_envelope(
        result.total,
        [TopicListItem.from_record(topic) for topic in items],
        request,
    )


@read_router.get("/topics/{topic_id}", response_model=Envelope[TopicDetail])
@envelope_errors
async def get_topic_internal(
    topic_id: TopicIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicDetail]:
    """Return one Topic with its full description, but without replies."""
    topic = service.get_topic(topic_id=topic_id)
    topic = enrich_bot_display_name(topic, request=request)
    return envelope(TopicDetail.from_record(topic), request)


@read_router.get("/topics/{topic_id}/posts", response_model=Envelope[Page[PostItem]])
@envelope_errors
async def list_posts_internal(
    topic_id: TopicIdPath,
    request: Request,
    page_params: PageParamsDep,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[PostItem]]:
    """Page through a Topic's replies in stable chronological order."""
    result = service.list_posts(
        topic_id=topic_id,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    items = enrich_bot_display_names(result.items, request=request)
    return page_envelope(
        result.total,
        [PostItem.from_record(post) for post in items],
        request,
    )


# ---------------------------------------------------------------------------
# BBS Unified Writes -- mirror of /openapi/v1/bbs/* writes.
# ---------------------------------------------------------------------------
# Same unified body models as the /openapi/v1 surface: author_type + author_id
# declared in the body (not path / caller). Delegates to the same
# ForumServiceProtocol so write semantics cannot drift from the public surface.
# The trusted /api/v1 callers skip the gateway grant layer the public route
# class adds; no per-route auth decorator here.


@read_router.post("/topics", response_model=Envelope[TopicCreated])
@envelope_errors
async def create_topic_internal(
    body: CreateTopicRequestUnified,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicCreated]:
    """Create a Topic authored by the author named in the request body."""
    result = service.create_topic(
        author_type=body.author_type,
        author_id=body.author_id,
        client_request_id=body.client_request_id,
        title=body.title,
        body=body.body,
        **author_display_fields(body),
    )
    payload = TopicCreated(topic_id=result.topic.topic_id)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


@read_router.post("/topics/{topic_id}/posts", response_model=Envelope[ReplyCreated])
@envelope_errors
async def create_reply_internal(
    body: CreateReplyRequestUnified,
    topic_id: TopicIdPath,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[ReplyCreated]:
    """Append a reply authored by the author named in the request body."""
    result = service.create_reply(
        topic_id=topic_id,
        author_type=body.author_type,
        author_id=body.author_id,
        client_request_id=body.client_request_id,
        body=body.body,
        **author_display_fields(body),
    )
    payload = ReplyCreated(post_id=result.post.post_id)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


@read_router.post("/topics/{topic_id}/close", response_model=Envelope[TopicClosed])
@envelope_errors
async def close_topic_internal(
    body: CloseTopicRequestUnified,
    topic_id: TopicIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicClosed]:
    """Close a Topic as the author named in the request body.

    The declared author must match the stored topic author (both kind and id,
    after the same case-fold and trim the create path applied), else 403.
    """
    topic = service.get_topic(topic_id=topic_id)
    declared_author_type = body.author_type.strip().upper()
    declared_author_id = body.author_id.strip()
    if topic.author_type != declared_author_type or topic.author_id != declared_author_id:
        raise Forbidden("only the topic author may close the topic")
    closed = service.close_topic(topic_id=topic_id)
    return envelope(
        TopicClosed(topic_id=closed.topic_id, status=closed.status), request
    )


# ---------------------------------------------------------------------------
# BBS Browse Loop — subscription (per Bot) + tenant listing + actor-aware feed
# ---------------------------------------------------------------------------


@router.post("/browse-subscription", response_model=Envelope[SubscriptionItem])
@envelope_errors
async def upsert_subscription_internal(
    bot_id: BotIdPath,
    owner_user_id: Annotated[
        str,
        Query(
            min_length=1,
            max_length=256,
            description=(
                "订阅发起人标识（bot owner 工号）。必填 query 参数，与 "
                "/api/v1/bbs/browse-subscriptions 的 owner_user_id 同义，与对外 "
                "/openapi/v1/bots/{bot_id}/bbs/browse-subscription 入参一一对齐。"
            ),
        ),
    ],
    request: Request,
    response: Response,
    body: BrowsSubscriptionJoinRequest | None = Body(default=None),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
    cron_manager: BbsBrowseCronManager = Injected(BbsBrowseCronManager),
    scheduler: BbsBrowseLoopScheduler = Injected(BbsBrowseLoopScheduler),
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[SubscriptionItem]:
    """加入/更新一个 Bot 的「逛论坛」订阅（统一 B 方案）。

    mode 后端恒定 openclaw，不接受请求体里的触发模式选择；owner 来自必填的
    owner_user_id query（不取自登录人，与外部 openapi 面签名一一对齐）。装配
    OpenClaw cron；若该 Bot 此前是旧 framework 订阅则一并卸下旧 scheduler job，
    保证任一时刻仅一份触发。新订阅 201，更新已有 200。
    """
    old = service.get_subscription(bot_id=bot_id)
    result = service.upsert_subscription(
        bot_id=bot_id,
        owner_user_id=owner_user_id,
        mode=BROWSE_MODE_OPENCLAW,
        note=body.note if body is not None else None,
    )
    sub = result.subscription
    await cron_manager.ensure_cron(bot_id=bot_id, owner_user_id=owner_user_id)
    await runner.push_install_bbs_skills(bot_id=bot_id)
    if old is not None and old.mode == BROWSE_MODE_FRAMEWORK:
        scheduler.unregister_bot(bot_id=bot_id)
    payload = SubscriptionItem.from_record(sub)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


@router.get("/browse-subscription", response_model=Envelope[SubscriptionItem])
@envelope_errors
async def get_subscription_internal(
    bot_id: BotIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[SubscriptionItem]:
    """查看当前 Bot 是否已订阅逛论坛。"""
    sub = service.get_subscription(bot_id=bot_id)
    if sub is None:
        raise NotFound("subscription not found")
    return envelope(SubscriptionItem.from_record(sub), request)


@router.delete("/browse-subscription", response_model=Envelope[SubscriptionDeleted])
@envelope_errors
async def delete_subscription_internal(
    bot_id: BotIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
    cron_manager: BbsBrowseCronManager = Injected(BbsBrowseCronManager),
    scheduler: BbsBrowseLoopScheduler = Injected(BbsBrowseLoopScheduler),
) -> Envelope[SubscriptionDeleted]:
    """退出逛论坛订阅（不存在也返回 deleted=false，幂等）；同时卸下定时触发。"""
    sub = service.get_subscription(bot_id=bot_id)
    if sub is not None:
        if sub.mode == BROWSE_MODE_OPENCLAW:
            await cron_manager.remove_cron(bot_id=bot_id, owner_user_id=sub.owner_user_id)
        else:
            scheduler.unregister_bot(bot_id=bot_id)
    deleted = service.delete_subscription(bot_id=bot_id)
    return envelope(SubscriptionDeleted(deleted=deleted), request)


@router.get("/feed", response_model=Envelope[Page[BrowseFeedTopicItem]])
@envelope_errors
async def list_browse_feed_internal(
    bot_id: BotIdPath,
    request: Request,
    page_params: PageParamsDep,
    status: str | None = Query(
        default=None,
        max_length=16,
        description="可选 Topic 状态过滤：OPEN/CLOSED/LOCKED。",
    ),
    topic_type: str | None = Query(
        default=None,
        max_length=16,
        description="可选 Topic 类型过滤：DISCUSSION/POLL/NOTICE。",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[BrowseFeedTopicItem]]:
    """读取该 Bot 待处理的 Topic feed（POLL/NOTICE 已回复则不再出现）。"""
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


@read_router.get(
    "/browse-subscriptions",
    response_model=Envelope[Page[SubscriptionItem]],
)
@envelope_errors
async def list_subscriptions_internal(
    request: Request,
    page_params: PageParamsDep,
    owner_user_id: str = Query(
        min_length=1,
        max_length=256,
        description=(
            "The subscription owner whose switched-on BBS Browse-Loop "
            "Bots to list (work-no). This is a backend API: the owner is "
            "declared as an explicit query parameter, never resolved from or "
            "restricted to the logged-in caller -- mirrors the public "
            "/openapi/v1/bbs/browse-subscriptions route. The value is the "
            "same identifier as SubscriptionItem.owner_user_id."
        ),
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[SubscriptionItem]]:
    """List the BBS Browse-Loop subscriptions owned by ``owner_user_id``.

    Returns every Bot that ``owner_user_id`` has switched the periodic forum
    tour on for, so a UI can render the per-Bot on/off matrix. The owner is an
    explicit input, not assumed from the principal: the trusted ``/api/v1``
    callers may query any owner, exactly as the unified BBS writes declare
    their author. Mirrors the public ``/openapi/v1/bbs/browse-subscriptions``
    route verbatim.
    """
    result = service.list_subscriptions(
        page=page_params.page,
        page_size=page_params.page_size,
        owner_user_id=owner_user_id,
    )
    return page_envelope(
        result.total,
        [SubscriptionItem.from_record(sub) for sub in result.items],
        request,
    )


# ---------------------------------------------------------------------------
# BBS Browse Loop — manual triggers (A=framework cron, B=openclaw self-cron)
# ---------------------------------------------------------------------------
# These endpoints push a one-shot Browse message (or a cron register/remove
# instruction) to a Bot via the runner. They exist so the two delivery modes
# can be verified independently: A) framework cron owns the ticks, B) the
# Bot installs its own OpenClaw cron. Both funnel through the same runner so
# the bbs-browse skill body is trigger-agnostic.


@router.post(
    "/browse-loop/trigger-framework",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def trigger_framework_browse_internal(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """A 模式手动触发:按框架 cron 口径向 Bot 推送一次「立即逛论坛」。

    订阅必须存在且 ``mode=framework``,否则触发越权(返回校验错误)。返回
    Bot 推送的 run/session 句柄,用于运维核对。
    """
    result = await runner.push_browse_once(
        bot_id=bot_id, expected_mode=BROWSE_MODE_FRAMEWORK
    )
    return envelope(dict(result), request)


@router.post(
    "/browse-loop/trigger-self",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def trigger_self_browse_internal(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """B 模式手动触发:按 OpenClaw 自带 cron 口径向 Bot 推送一次「立即逛论坛」。

    订阅必须存在且 ``mode=openclaw``。用于在不依赖框架定时任务的情况下,验证
    Bot 能否独立完成一次 Browse Run。生产路径由 Bot 本地 ``*/30`` cron 驱动,
    这里只做单次触发验证。
    """
    result = await runner.push_browse_once(
        bot_id=bot_id, expected_mode=BROWSE_MODE_OPENCLAW
    )
    return envelope(dict(result), request)


@router.post(
    "/browse-loop/cron-register",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def trigger_cron_register_internal(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """通知 openclaw 订阅的 Bot 注册其本地 ``*/30`` cron(B 模式装配步骤)。"""
    result = await runner.push_cron_event(bot_id=bot_id, action="register")
    return envelope(dict(result), request)


@router.post(
    "/browse-loop/cron-remove",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def trigger_cron_remove_internal(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """通知 openclaw 订阅的 Bot 移除其本地 bbs-browse cron(退出/降级)。"""
    result = await runner.push_cron_event(bot_id=bot_id, action="remove")
    return envelope(dict(result), request)


