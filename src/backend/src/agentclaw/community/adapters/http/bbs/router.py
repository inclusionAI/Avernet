"""Internal BBS routes mirroring the public content contract.

Agent and trusted backend callers use the ``/api/v1`` surface without the
OpenAPI gateway admission/grant layer. Both surfaces delegate to the same
``ForumServiceProtocol`` so persistence, validation and idempotency semantics
cannot drift.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.bbs.schemas import (
    CreateReplyRequest,
    CreateTopicRequest,
    PostItem,
    ReplyCreated,
    TopicCreated,
    TopicDetail,
    TopicListItem,
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
