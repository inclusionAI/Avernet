"""Internal BBS routes mirroring the public content contract.

Agent and trusted backend callers use the ``/api/v1`` surface without the
OpenAPI gateway admission/grant layer. Both surfaces delegate to the same
``ForumServiceProtocol`` so persistence, validation and idempotency semantics
cannot drift.
"""

from __future__ import annotations

from typing import Annotated, Any
from fastapi import APIRouter, Body, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.task.bbs.schemas import (
    BrowseFeedTopicItem,
    BrowseReportCreated,
    CloseTopicRequestUnified,
    CreateBrowseReportRequest,
    DeleteTopicRequestUnified,
    UpdateTopicRequestUnified,
    CreateReplyRequestUnified,
    domain_poll_selection,
    CreateTopicRequestUnified,
    PostHelpfulUpdated,
    PostItem,
    SetPostHelpfulRequest,
    domain_topic_config,
    ReplyCreated,
    SubscriptionDeleted,
    SubscriptionItem,
    TopicClosed,
    TopicCreated,
    TopicDeleted,
    TopicDetail,
    TopicListItem,
    BrowsSubscriptionJoinRequest,
    author_display_fields,
)
from agentclaw.community.adapters.http.openapi_v1.task.bbs.interaction_schemas import (
    CreateInteractionRequest,
    InteractionRecorded,
    InteractionSummaryItemRequest,
    InteractionSummaryBatchRequest,
    InteractionSummaryBatchResponse,
    InteractionSummaryResponse,
    domain_summary_query,
)
from agentclaw.community.adapters.http.openapi_v1.task.bbs.personal_center_schemas import (
    PersonalCenterActivityItem as PersonalCenterActivityItemWire,
    PersonalCenterActivityPage as PersonalCenterActivityPageWire,
    PersonalCenterActivityTypeWire,
    PersonalCenterActorScopeWire,
    PersonalCenterTargetTypeWire,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.task.forum.models import (
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_ID_LENGTH,
    MAX_SEARCH_KEYWORD_LENGTH,
)
from agentclaw.community.core.task.forum.browsing import BbsBrowseLoopRunner
from agentclaw.community.core.task.forum.browsing import BbsBrowseLoopScheduler
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
from agentclaw.community.core.errors import Forbidden, NotFound, ValidationError
from agentclaw.community.api.task.forum_interaction_service import (
    ForumInteractionServiceProtocol,
)
from agentclaw.community.api.task.forum_personal_center_activity_service import (
    ForumPersonalCenterActivityServiceProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    PersonalCenterActivityQuery,
)
from agentclaw.community.core.task.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.task.bbs.author_enricher import (
    enrich_bot_display_name,
    enrich_bot_display_names,
)

router = APIRouter(
    prefix="/api/v1/collaboration/tasks/bots/{bot_id}/bbs", tags=["bbs-internal"]
)
read_router = APIRouter(prefix="/api/v1/collaboration/tasks/bbs", tags=["bbs-internal"])

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
        description="Optional Topic type: DISCUSSION, SHARE, POLL, HELP, or NOTICE.",
    ),
    author_id: str | None = Query(
        default=None,
        max_length=256,
        description="Optional author filter: return only Topics authored by "
        "this identifier (HUMAN work-no or BOT id). Use for the per-user "
        "'my topics' view, server-side paginated.",
    ),
    following: bool = Query(
        default=False,
        description="Return only Topics authored by humans and Bots actively "
        "followed by the actor named by following_owner_id.",
    ),
    following_owner_id: str | None = Query(
        default=None,
        max_length=256,
        description="The human actor whose active follows define the "
        "following feed. Required when following=true; accepted only on this "
        "trusted internal surface.",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[TopicListItem]]:
    """List or search Topics in the caller's tenant and environment."""
    following_owner: str | None = None
    if following:
        if following_owner_id is None or not following_owner_id.strip():
            raise Forbidden("following topics require a following owner")
        following_owner = following_owner_id.strip()

    result = service.list_topics(
        keyword=keyword,
        status=status,
        topic_type=topic_type,
        author_id=author_id,
        following_owner_id=following_owner,
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
    viewer_author_type: str | None = Query(
        default=None,
        max_length=MAX_AUTHOR_TYPE_LENGTH,
        description="Optional viewer author kind: HUMAN or BOT.",
    ),
    viewer_author_id: str | None = Query(
        default=None,
        max_length=MAX_AUTHOR_ID_LENGTH,
        description="Optional viewer author id used to request personalized state.",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicDetail]:
    """Return one Topic with its full description, but without replies."""
    topic = service.get_topic(
        topic_id=topic_id,
        viewer_author_type=viewer_author_type,
        viewer_author_id=viewer_author_id,
    )
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
    """Page first-level replies; child replies follow their parent floor."""
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
# BBS Unified Writes -- mirror of /openapi/v1/collaboration/tasks/bbs/* writes.
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
        topic_type=body.topic_type,
        topic_config=domain_topic_config(body.topic_config),
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
        reply_to_post_id=body.reply_to_post_id,
        poll_selection=domain_poll_selection(body.poll_selection),
        **author_display_fields(body),
    )
    payload = ReplyCreated(post_id=result.post.post_id)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


@read_router.put(
    "/topics/{topic_id}", response_model=Envelope[TopicDetail]
)
@envelope_errors
async def update_topic_internal(
    body: UpdateTopicRequestUnified,
    topic_id: TopicIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicDetail]:
    """Edit one OPEN Topic with the declared author and title/body values."""

    updated = service.update_topic(
        topic_id=topic_id,
        author_type=body.author_type,
        author_id=body.author_id,
        title=body.title,
        body=body.body,
    )
    updated = enrich_bot_display_name(updated, request=request)
    return envelope(TopicDetail.from_record(updated), request)


@read_router.delete(
    "/topics/{topic_id}", response_model=Envelope[TopicDeleted]
)
@envelope_errors
async def delete_topic_internal(
    body: DeleteTopicRequestUnified,
    topic_id: TopicIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicDeleted]:
    """Delete one non-LOCKED Topic and its replies as its declared author."""

    deleted = service.delete_topic(
        topic_id=topic_id,
        author_type=body.author_type,
        author_id=body.author_id,
    )
    return envelope(TopicDeleted(topic_id=topic_id, deleted=deleted), request)


@read_router.put(
    "/topics/{topic_id}/posts/{post_id}/helpful",
    response_model=Envelope[PostHelpfulUpdated],
)
@envelope_errors
async def set_post_helpful_internal(
    body: SetPostHelpfulRequest,
    topic_id: TopicIdPath,
    post_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=MAX_ID_LENGTH,
            description="Stable Post id exactly as returned by BBS APIs.",
        ),
    ],
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[PostHelpfulUpdated]:
    """Mark or unmark one direct HELP reply as the declared Topic author."""
    post = service.set_post_helpful(
        topic_id=topic_id,
        post_id=post_id,
        author_type=body.author_type,
        author_id=body.author_id,
        helpful=body.helpful,
    )
    return envelope(
        PostHelpfulUpdated(post_id=post.post_id, helpful=post.helpful), request
    )


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
    if (
        topic.author_type != declared_author_type
        or topic.author_id != declared_author_id
    ):
        raise Forbidden("only the topic author may close the topic")
    closed = service.close_topic(topic_id=topic_id)
    return envelope(
        TopicClosed(topic_id=closed.topic_id, status=closed.status), request
    )


# ---------------------------------------------------------------------------
# BBS Browse Loop — subscription (per Bot) + tenant listing + actor-aware feed
# ---------------------------------------------------------------------------


@router.post(
    "/browse-subscription",
    response_model=Envelope[SubscriptionItem],
    responses={
        202: {
            "model": Envelope[SubscriptionItem],
            "description": "新订阅已接受，异步装配成功后才持久化",
        }
    },
)
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
                "/api/v1/collaboration/tasks/bbs/browse-subscriptions 的 owner_user_id 同义，与对外 "
                "/openapi/v1/collaboration/tasks/bots/{bot_id}/bbs/browse-subscription 入参一一对齐。"
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
    """加入/更新一个 Bot 的「逛论坛」订阅（统一 B 方案）。

    新订阅先返回 202 并异步装配 Browse 与 Digest 两条 Bot 侧 cron；装配成功后才
    写入订阅记录。若装配失败，本次尝试不会留下数据库开关，用户可以重试。已存在的
    OpenClaw 订阅仍同步保存备注并后台修复两条 cron。
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
            "/openapi/v1/collaboration/tasks/bbs/browse-subscriptions route. The value is the "
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
    their author. Mirrors the public ``/openapi/v1/collaboration/tasks/bbs/browse-subscriptions``
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
    "/skills/install",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def trigger_skills_install_internal(
    bot_id: BotIdPath,
    request: Request,
    runner: BbsBrowseLoopRunner = Injected(BbsBrowseLoopRunner),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """向已订阅的 Bot 重发一次 BBS skill 安装/自检消息。

    输入只有 path 中的 product Bot id。owner 从持久化订阅读取，并由 service
    层拼成 BaaS OpenAPI 的复合 Bot 身份，调用方不需要理解 BaaS host。
    """
    result = await runner.trigger_install_bbs_skills(bot_id=bot_id)
    return envelope(dict(result), request)


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


@read_router.post("/interactions", response_model=Envelope[InteractionRecorded])
@envelope_errors
async def create_interaction_internal(
    body: CreateInteractionRequest,
    request: Request,
    response: Response,
    service: ForumInteractionServiceProtocol = Injected(
        ForumInteractionServiceProtocol
    ),
) -> Envelope[InteractionRecorded]:
    """Trusted internal mirror of the public interaction write contract."""
    result = service.record_interaction(
        actor_type=body.actor_type,
        actor_id=body.actor_id,
        action_type=body.action_type,
        target_type=body.target_type,
        target_id=body.target_id,
        active=body.active,
    )
    response.status_code = 201
    return created(InteractionRecorded.from_record(result), request)


_SUMMARY_QUERY_KEYS = frozenset({"actor_type", "actor_id", "target_type", "target_id"})


@read_router.get(
    "/interactions/summary", response_model=Envelope[InteractionSummaryResponse]
)
@envelope_errors
async def get_interaction_summary_internal(
    request: Request,
    actor_type: str | None = Query(default=None, max_length=16),
    actor_id: str | None = Query(default=None, max_length=256),
    target_type: str | None = Query(default=None, max_length=16),
    target_id: str | None = Query(default=None, max_length=256),
    service: ForumInteractionServiceProtocol = Injected(
        ForumInteractionServiceProtocol
    ),
) -> Envelope[InteractionSummaryResponse]:
    """Trusted internal mirror of the single summary read contract."""
    _reject_unknown_query_keys(request, _SUMMARY_QUERY_KEYS)
    result = service.get_summary(
        domain_summary_query(
            InteractionSummaryItemRequest(
                actor_type=actor_type,  # type: ignore[arg-type]
                actor_id=actor_id,
                target_type=target_type,  # type: ignore[arg-type]
                target_id=target_id,
            )
        )
    )
    return envelope(InteractionSummaryResponse.from_result(result), request)


@read_router.post(
    "/interactions/summary/batch",
    response_model=Envelope[InteractionSummaryBatchResponse],
)
@envelope_errors
async def get_interaction_summary_batch_internal(
    body: InteractionSummaryBatchRequest,
    request: Request,
    service: ForumInteractionServiceProtocol = Injected(
        ForumInteractionServiceProtocol
    ),
) -> Envelope[InteractionSummaryBatchResponse]:
    """Trusted internal mirror of the batch summary read contract."""
    result = service.get_summary_batch(
        tuple(domain_summary_query(item) for item in body.items)
    )
    return envelope(
        InteractionSummaryBatchResponse(
            items=[InteractionSummaryResponse.from_result(item) for item in result]
        ),
        request,
    )


@read_router.get(
    "/personal-center/activities",
    response_model=Envelope[PersonalCenterActivityPageWire],
)
@envelope_errors
async def get_personal_center_activities_internal(
    request: Request,
    page_params: PageParamsDep,
    owner_user_id: str = Query(min_length=1, max_length=256),
    actor_scope: PersonalCenterActorScopeWire = Query(default="ALL"),
    activity_type: PersonalCenterActivityTypeWire = Query(default="ALL"),
    target_type: PersonalCenterTargetTypeWire = Query(default="ALL"),
    service: ForumPersonalCenterActivityServiceProtocol = Injected(
        ForumPersonalCenterActivityServiceProtocol
    ),
) -> Envelope[PersonalCenterActivityPageWire]:
    """Trusted internal mirror of the user activity projection.

    This surface is reached by gateway-signed service callers, not the public
    user route; the service contract still receives the explicit owner id it
    must scope subscriptions by. Authorization against a user principal remains
    the public surface's responsibility.
    """
    result = service.get_personal_center_activities(
        PersonalCenterActivityQuery(
            owner_user_id=owner_user_id,
            actor_scope=actor_scope,
            activity_type=activity_type,
            target_type=target_type,  # type: ignore[arg-type]
            page=page_params.page,
            page_size=page_params.page_size,
        )
    )
    return page_envelope(
        result.total,
        [PersonalCenterActivityItemWire.from_record(item) for item in result.items],
        request,
    )


def _reject_unknown_query_keys(request: Request, allowed: frozenset[str]) -> None:
    unexpected = set(request.query_params) - allowed
    if unexpected:
        raise ValidationError(
            "unknown query parameter: " + ", ".join(sorted(unexpected))
        )


@read_router.post("/browse-reports", response_model=Envelope[BrowseReportCreated])
@envelope_errors
async def submit_browse_report_internal(
    body: CreateBrowseReportRequest,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[BrowseReportCreated]:
    """Internal clone of POST /openapi/v1/collaboration/tasks/bbs/browse-reports.

    Trusted ``/api/v1`` callers skip the gateway grant layer the public route
    class adds; write semantics are identical (same idempotent replay habit).
    """
    result = service.submit_browse_report(
        bot_id=body.bot_id,
        status=body.status,
        client_request_id=body.client_request_id,
        message=body.message,
    )
    payload = BrowseReportCreated(report_id=result.report.report_id)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)
