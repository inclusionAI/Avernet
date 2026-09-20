"""BBS content group — topic and reply creation.

This route represents an authorized Bot author. The core model also supports
Human authors; author identity is derived by the adapter and never accepted in
the request body.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
)
from agentclaw.community.core.forum.models import AUTHOR_TYPE_BOT
from agentclaw.community.core.forum.service_protocol import ForumServiceProtocol
from agentclaw.community.di import Injected

from .schemas import (
    CreateReplyRequest,
    CreateTopicRequest,
    ReplyCreated,
    TopicCreated,
)

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/bbs",
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


@router.post("/topics", response_model=Envelope[TopicCreated])
@envelope_errors
async def create_topic(
    body: CreateTopicRequest,
    bot_id: BotIdPath,
    owner_id: OwnerIdDep,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[TopicCreated]:
    """Create a Topic, or replay an earlier idempotent write.

    HTTP 201 means this request created the Topic; HTTP 200 means an earlier
    request with the same idempotency key already created it.
    """
    result = service.create_topic(
        author_type=AUTHOR_TYPE_BOT,
        author_id=bot_id,
        client_request_id=body.client_request_id,
        title=body.title,
        body=body.body,
    )
    payload = TopicCreated(topic_id=result.topic.topic_id)
    response.status_code = 201 if result.created else 200
    return created(payload, request) if result.created else envelope(payload, request)


@router.post(
    "/topics/{topic_id}/replies",
    response_model=Envelope[ReplyCreated],
)
@envelope_errors
async def create_reply(
    body: CreateReplyRequest,
    bot_id: BotIdPath,
    topic_id: TopicIdPath,
    owner_id: OwnerIdDep,
    request: Request,
    response: Response,
    service: ForumServiceProtocol = Injected(ForumServiceProtocol),
) -> Envelope[ReplyCreated]:
    """Append one reply to a Topic, or replay an earlier idempotent write.

    HTTP 201 means this request created the reply; HTTP 200 means an earlier
    request with the same idempotency key already created it.
    """
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
