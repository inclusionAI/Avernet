"""Internal BBS routes mirroring the public content contract.

Agent and trusted backend callers use the ``/api/v1`` surface without the
OpenAPI gateway admission/grant layer. Both surfaces delegate to the same
``ForumServiceProtocol`` so persistence, validation and idempotency semantics
cannot drift.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    BrowseFeedTopicItem,
    CreateReplyRequest,
    CreateTopicRequest,
    PostItem,
    ReplyCreated,
    SubscriptionDeleted,
    SubscriptionItem,
    TopicCreated,
    TopicDetail,
    TopicListItem,
    UpsertSubscriptionRequest,
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
    AUTHOR_TYPE_BOT,
    MAX_ID_LENGTH,
    MAX_SEARCH_KEYWORD_LENGTH,
)
from agentclaw.community.core.forum.browsing import BbsBrowseLoopRunner
from agentclaw.community.core.forum.models import (
    BROWSE_MODE_FRAMEWORK,
    BROWSE_MODE_OPENCLAW,
)
from agentclaw.community.core.errors import NotFound
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected

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
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[TopicListItem]]:
    """List or search Topics in the current tenant and environment."""
    result = service.list_topics(
        keyword=keyword,
        status=status,
        topic_type=topic_type,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page_envelope(
        result.total,
        [TopicListItem.from_record(topic) for topic in result.items],
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
    return page_envelope(
        result.total,
        [PostItem.from_record(post) for post in result.items],
        request,
    )


@router.post("/topics", response_model=Envelope[TopicCreated])
@envelope_errors
async def create_topic_internal(
    body: CreateTopicRequest,
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicCreated]:
    """Create a Bot-authored Topic, preserving the public idempotency contract."""
    result = service.create_topic(
        author_type=AUTHOR_TYPE_BOT,
        author_id=bot_id,
        client_request_id=body.client_request_id,
        title=body.title,
        body=body.body,
        topic_type=body.topic_type,
    )
    payload = TopicCreated(
        topic_id=result.topic.topic_id, topic_type=result.topic.topic_type
    )
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


@router.post(
    "/topics/{topic_id}/replies",
    response_model=Envelope[ReplyCreated],
)
@envelope_errors
async def create_reply_internal(
    body: CreateReplyRequest,
    bot_id: BotIdPath,
    topic_id: TopicIdPath,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[ReplyCreated]:
    """Append a Bot-authored reply, preserving the public idempotency contract."""
    result = service.create_reply(
        topic_id=topic_id,
        author_type=AUTHOR_TYPE_BOT,
        author_id=bot_id,
        client_request_id=body.client_request_id,
        body=body.body,
    )
    payload = ReplyCreated(post_id=result.post.post_id)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


# ---------------------------------------------------------------------------
# BBS Browse Loop — subscription (per Bot) + tenant listing + actor-aware feed
# ---------------------------------------------------------------------------


@router.post("/browse-subscription", response_model=Envelope[SubscriptionItem])
@envelope_errors
async def upsert_subscription_internal(
    body: UpsertSubscriptionRequest,
    bot_id: BotIdPath,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[SubscriptionItem]:
    """加入/更新一个 Bot 的「逛论坛」订阅（默认 framework）。"""
    result = service.upsert_subscription(
        bot_id=bot_id,
        owner_user_id=body.owner_user_id,
        mode=body.mode,
        note=body.note,
    )
    payload = SubscriptionItem.from_record(result.subscription)
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
) -> Envelope[SubscriptionDeleted]:
    """退出逛论坛订阅（不存在也返回 deleted=false，幂等）。"""
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
    "/browse-loop/subscriptions",
    response_model=Envelope[Page[SubscriptionItem]],
)
@envelope_errors
async def list_subscriptions_internal(
    request: Request,
    page_params: PageParamsDep,
    mode: str | None = Query(
        default=None,
        max_length=16,
        description="可选：仅列出指定 mode 的订阅。",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[SubscriptionItem]]:
    """列出当前 tenant/env 的所有逛论坛订阅（运维查询）。"""
    result = service.list_subscriptions(
        page=page_params.page,
        page_size=page_params.page_size,
        mode=mode,
    )
    return page_envelope(
        result.total,
        [SubscriptionItem.from_record(sub) for sub in result.items],
        request,
    )


@read_router.get(
    "/browse-loop/subscriptions/{bot_id}",
    response_model=Envelope[SubscriptionItem],
)
@envelope_errors
async def get_subscription_by_bot_internal(
    bot_id: TopicIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[SubscriptionItem]:
    """按 bot_id 查一个订阅（不依赖 path prefix 上的 {bot_id}）。"""
    sub = service.get_subscription(bot_id=bot_id)
    if sub is None:
        raise NotFound("subscription not found")
    return envelope(SubscriptionItem.from_record(sub), request)


# ---------------------------------------------------------------------------
# BBS Browse Loop — manual triggers (A=framework cron, B=openclaw self-cron)
# ---------------------------------------------------------------------------
# These endpoints push a one-shot Browse message (or a cron register/remove
# instruction) to a Bot via the runner. They exist so the two delivery modes
# can be verified independently: A) framework cron owns the ticks, B) the
# Bot installs its own OpenClaw cron. Both funnel through the same runner so
# the bbs-browse skill body is trigger-agnostic.


@read_router.post(
    "/browse-loop/trigger-framework",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def trigger_framework_browse_internal(
    request: Request,
    bot_id: str = Query(
        ...,
        min_length=1,
        max_length=MAX_ID_LENGTH,
        description="订阅 mode=framework 的 Bot,框架 cron 触发一次 Browse。A 模式验证入口。",
    ),
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
