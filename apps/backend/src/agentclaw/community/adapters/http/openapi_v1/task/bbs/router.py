"""Public BBS Topic reads plus unified, explicit-author content writes."""

from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
    USER_SCOPED_ERROR_RESPONSES,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.errors import Forbidden, ValidationError

from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.api.task.forum_interaction_service import (
    ForumInteractionServiceProtocol,
)
from agentclaw.community.api.task.forum_personal_center_activity_service import (
    ForumPersonalCenterActivityServiceProtocol,
)
from agentclaw.community.core.task.forum.interaction.models import (
    PersonalCenterActivityQuery,
)
from agentclaw.community.core.task.forum.models import (
    MAX_AUTHOR_ID_LENGTH,
    MAX_AUTHOR_TYPE_LENGTH,
    MAX_SEARCH_KEYWORD_LENGTH,
)
from agentclaw.community.core.task.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.task.bbs.author_enricher import (
    enrich_bot_display_name,
    enrich_bot_display_names,
)

from .interaction_schemas import (
    CreateInteractionRequest,
    InteractionRecorded,
    InteractionSummaryBatchRequest,
    InteractionSummaryItemRequest,
    InteractionSummaryBatchResponse,
    InteractionSummaryResponse,
    domain_summary_query,
)
from .personal_center_schemas import (
    PersonalCenterActivityItem,
    PersonalCenterActivityPage,
    PersonalCenterActivityTypeWire,
    PersonalCenterActorScopeWire,
    PersonalCenterTargetTypeWire,
)

from .schemas import (
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
    SubscriptionItem,
    TopicClosed,
    TopicCreated,
    TopicDeleted,
    TopicDetail,
    TopicListItem,
    author_display_fields,
)

read_router = APIRouter(
    prefix="/openapi/v1/collaboration/tasks/bbs",
    tags=["bbs"],
    route_class=PublicAPIRoute,
)

TopicIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        description="Stable topic id exactly as returned by BBS APIs.",
    ),
]


@read_router.get(
    "/topics",
    response_model=Envelope[Page[TopicListItem]],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def list_topics(
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
        "followed by the verified end user.",
    ),
    principal: Principal = Depends(require_principal),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[TopicListItem]]:
    """List or search Topics in the caller's tenant and environment."""
    following_owner_id: str | None = None
    if following:
        if not principal.has_user:
            raise Forbidden("following topics require an end-user caller")
        following_owner_id = principal.user_id

    result = service.list_topics(
        keyword=keyword,
        status=status,
        topic_type=topic_type,
        author_id=author_id,
        following_owner_id=following_owner_id,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    items = enrich_bot_display_names(result.items, request=request)
    return page_envelope(
        result.total,
        [TopicListItem.from_record(topic) for topic in items],
        request,
    )


@read_router.get(
    "/topics/{topic_id}",
    response_model=Envelope[TopicDetail],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def get_topic(
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


@read_router.get(
    "/topics/{topic_id}/posts",
    response_model=Envelope[Page[PostItem]],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def list_posts(
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


@read_router.post(
    "/topics",
    response_model=Envelope[TopicCreated],
    # Authorised on the route (any verified principal) so the principal-seam
    # test sees it; the author is declared in the body, not the caller.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def create_topic_unified(
    body: CreateTopicRequestUnified,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicCreated]:
    """Create a Topic authored by the author named in the request body.

    The author (`author_type` + `author_id`) is declared in the body, not
    inferred from the caller: this is a backend API and may be reached by
    application-to-application calls with no single human principal, so the
    surface records the author the caller declares. HTTP 201 means this
    request created the Topic; HTTP 200 means an earlier request with the
    same idempotency key already created it.
    """
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


@read_router.post(
    "/topics/{topic_id}/posts",
    response_model=Envelope[ReplyCreated],
    # Authorised on the route (any verified principal) so the principal-seam
    # test sees it; the author is declared in the body, not the caller.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def create_reply_unified(
    body: CreateReplyRequestUnified,
    topic_id: TopicIdPath,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[ReplyCreated]:
    """Append one reply authored by the author named in the request body.

    See `create_topic_unified`: the author is declared in the body, not
    derived from the caller. HTTP 201 means this request created the reply;
    HTTP 200 means an earlier request with the same idempotency key already did.
    """
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
    "/topics/{topic_id}",
    response_model=Envelope[TopicDetail],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def update_topic_unified(
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
    "/topics/{topic_id}",
    response_model=Envelope[TopicDeleted],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def delete_topic_unified(
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
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def set_post_helpful_unified(
    body: SetPostHelpfulRequest,
    topic_id: TopicIdPath,
    post_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
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


@read_router.post(
    "/topics/{topic_id}/close",
    response_model=Envelope[TopicClosed],
    # Authorised on the route (any verified principal) so the principal-seam
    # test sees it; the author is declared in the body, not the caller.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def close_topic_unified(
    body: CloseTopicRequestUnified,
    topic_id: TopicIdPath,
    request: Request,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicClosed]:
    """Close an open Topic as the author named in the request body.

    The declared author (`author_type` + `author_id`) must match the stored
    topic author -- both kind and id, after the same case-fold and trim the
    create path applied -- otherwise the request is refused with `403`.
    Idempotent for an already-closed topic and surfaces `409` (Conflict) for
    a `LOCKED` topic.
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


@read_router.get(
    "/browse-subscriptions",
    response_model=Envelope[Page[SubscriptionItem]],
    operation_id="list_bbs_browse_subscriptions",
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def list_bbs_browse_subscriptions(
    request: Request,
    page_params: PageParamsDep,
    owner_user_id: str = Query(
        min_length=1,
        max_length=256,
        description="The subscription owner whose switched-on BBS Browse-Loop "
        "Bots to list (work-no). This is a backend API: the owner is declared "
        "as an explicit query parameter, never resolved from or restricted to "
        "the logged-in caller -- app-to-app and cross-user callers may query "
        "any owner, exactly as the unified BBS writes declare their author. The "
        "value is the same identifier as SubscriptionItem.owner_user_id.",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[SubscriptionItem]]:
    """List the BBS Browse-Loop subscriptions owned by `owner_user_id`.

    Returns every Bot that `owner_user_id` has switched the periodic forum
    tour on for, so a UI can render the per-Bot on/off matrix: combine this
    list with GET /openapi/v1/bots (that owner full Bot list) and set membership
    on the returned `bot_id` values. The owner is an explicit input, not
    assumed from the principal -- a backend API, not a logged-in-user-scoped
    product.
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


@read_router.post(
    "/interactions",
    response_model=Envelope[InteractionRecorded],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def create_interaction(
    body: CreateInteractionRequest,
    request: Request,
    response: Response,
    service: ForumInteractionServiceProtocol = Injected(
        ForumInteractionServiceProtocol
    ),
) -> Envelope[InteractionRecorded]:
    """Append one valid BBS interaction event in the current environment."""
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


_SUMMARY_QUERY_KEYS = frozenset(
    {
        "actor_type",
        "actor_id",
        "target_type",
        "target_id",
    }
)


@read_router.get(
    "/interactions/summary",
    response_model=Envelope[InteractionSummaryResponse],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def get_interaction_summary(
    request: Request,
    actor_type: str | None = Query(
        default=None,
        max_length=16,
        description="Optional actor dimension type: HUMAN or BOT. Required with actor_id.",
    ),
    actor_id: str | None = Query(
        default=None,
        max_length=256,
        description="Optional stable HUMAN work-no or BOT id used as the actor dimension.",
    ),
    target_type: str | None = Query(
        default=None,
        max_length=16,
        description="Optional target dimension type: TOPIC, POST, HUMAN, or BOT. Required with target_id.",
    ),
    target_id: str | None = Query(
        default=None,
        max_length=256,
        description="Optional stable target id used as the target dimension.",
    ),
    service: ForumInteractionServiceProtocol = Injected(
        ForumInteractionServiceProtocol
    ),
) -> Envelope[InteractionSummaryResponse]:
    """Read one actor, target, or actor-target summary."""
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
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def get_interaction_summary_batch(
    body: InteractionSummaryBatchRequest,
    request: Request,
    service: ForumInteractionServiceProtocol = Injected(
        ForumInteractionServiceProtocol
    ),
) -> Envelope[InteractionSummaryBatchResponse]:
    """Read at most 100 mixed summary dimensions; this operation is read-only."""
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
    response_model=Envelope[PersonalCenterActivityPage],
    responses=USER_SCOPED_ERROR_RESPONSES,
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def get_personal_center_activities(
    request: Request,
    page_params: PageParamsDep,
    owner_user_id: str = Query(
        min_length=1,
        max_length=256,
        description="The current human owner whose own and authorized-Bot activities are returned.",
    ),
    actor_scope: PersonalCenterActorScopeWire = Query(
        default="ALL",
        description="Which activities to include: the current HUMAN, authorized Bots, or both.",
    ),
    activity_type: PersonalCenterActivityTypeWire = Query(
        default="ALL",
        description="Activity kind filter, or ALL for every supported activity including follows.",
    ),
    target_type: PersonalCenterTargetTypeWire = Query(
        default="ALL",
        description="Content or identity target type filter, or ALL for every supported target.",
    ),
    principal: Principal = Depends(require_principal),
    service: ForumPersonalCenterActivityServiceProtocol = Injected(
        ForumPersonalCenterActivityServiceProtocol
    ),
) -> Envelope[PersonalCenterActivityPage]:
    """Return the current Human's server-paged BBS activity summary."""
    if owner_user_id.strip() != principal.user_id:
        raise Forbidden("owner_user_id must match the current human")
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
        [PersonalCenterActivityItem.from_record(item) for item in result.items],
        request,
    )


def _reject_unknown_query_keys(request: Request, allowed: frozenset[str]) -> None:
    unexpected = set(request.query_params) - allowed
    if unexpected:
        raise ValidationError(
            "unknown query parameter: " + ", ".join(sorted(unexpected))
        )


@read_router.post(
    "/browse-reports",
    response_model=Envelope[BrowseReportCreated],
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def submit_browse_report_unified(
    body: CreateBrowseReportRequest,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[BrowseReportCreated]:
    """Report one BBS Browse-Loop run outcome.

    `client_request_id` is the bot-supplied idempotency key. HTTP 201 means
    the engine recorded the report; HTTP 200 means an earlier request with the
    same idempotency key already did and the existing `report_id` is returned
    unchanged (no rewrite).
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
