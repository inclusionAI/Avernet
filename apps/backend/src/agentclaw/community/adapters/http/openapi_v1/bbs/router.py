"""Public BBS Topic reads plus unified, explicit-author content writes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.forum.models import (
    MAX_SEARCH_KEYWORD_LENGTH,
)
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.bbs.author_enricher import (
    enrich_bot_display_name,
    enrich_bot_display_names,
)

from .schemas import (
    CloseTopicRequestUnified,
    CreateReplyRequestUnified,
    CreateTopicRequestUnified,
    PostItem,
    ReplyCreated,
    SubscriptionItem,
    TopicClosed,
    TopicCreated,
    TopicDetail,
    TopicListItem,
    author_display_fields,
)

read_router = APIRouter(
    prefix="/openapi/v1/bbs",
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

@read_router.get("/topics", response_model=Envelope[Page[TopicListItem]])
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
        description="Optional Topic type: DISCUSSION, POLL, or NOTICE.",
    ),
    author_id: str | None = Query(
        default=None,
        max_length=256,
        description="Optional author filter: return only Topics authored by "
        "this identifier (HUMAN work-no or BOT id). Use for the per-user "
        "\'my topics\' view, server-side paginated.",
    ),
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[Page[TopicListItem]]:
    """List or search Topics in the caller's tenant and environment."""
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
async def get_topic(
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
async def list_posts(
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

    The author (``author_type`` + ``author_id``) is declared in the body, not
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

    See ``create_topic_unified``: the author is declared in the body, not
    derived from the caller. HTTP 201 means this request created the reply;
    HTTP 200 means an earlier request with the same idempotency key already did.
    """
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

    The declared author (``author_type`` + ``author_id``) must match the stored
    topic author -- both kind and id, after the same case-fold and trim the
    create path applied -- otherwise the request is refused with ``403``.
    Idempotent for an already-closed topic and surfaces ``409`` (Conflict) for
    a ``LOCKED`` topic.
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
    """List the BBS Browse-Loop subscriptions owned by ``owner_user_id``.

    Returns every Bot that ``owner_user_id`` has switched the periodic forum
    tour on for, so a UI can render the per-Bot on/off matrix: combine this
    list with GET /openapi/v1/bots (that owner full Bot list) and set membership
    on the returned ``bot_id`` values. The owner is an explicit input, not
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
