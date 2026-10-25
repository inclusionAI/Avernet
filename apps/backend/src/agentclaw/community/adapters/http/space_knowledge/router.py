"""Authenticated browser API; identity and sync revisions are never client input."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.space_knowledge_service import (
    SpaceKnowledgeServiceProtocol,
)
from agentclaw.community.core.spaces.errors import (
    SpaceAccessDeniedError,
    SpaceNotFoundError,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBaseResult,
    KnowledgeBasePage,
    SpaceKnowledgeError,
)

router = APIRouter(
    prefix="/api/spaces/{space_id}/knowledge-bases", tags=["space-knowledge"]
)
SpaceId = Annotated[int, Path(ge=1)]
KnowledgeId = Annotated[
    str, Path(min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f\x7f/]+$")
]
_CODES = {
    "IDENTITY_REQUIRED": 401,
    "SPACE_MEMBER_REQUIRED": 403,
    "KB_PERMISSION_DENIED": 403,
    "RESOURCE_NOT_FOUND": 404,
    "SPACE_NOT_SYNCED": 409,
    "SPACE_REVISION_MISMATCH": 409,
    "CREATE_REQUEST_CONFLICT": 409,
    "KB_DELETED": 410,
    "INVALID_INPUT": 400,
    "SPACE_SYNC_UNAVAILABLE": 503,
    "DEPENDENCY_UNAVAILABLE": 503,
}


class CreateKnowledgeBaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f\x7f/]+$"
    )
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    description: str = Field(default="", max_length=1000)


async def _invoke(
    operation: Callable[[], KnowledgeBaseResult | KnowledgeBasePage],
    response: Response,
):
    try:
        result = await run_in_threadpool(operation)
    except SpaceNotFoundError:
        raise HTTPException(404, detail={"code": "RESOURCE_NOT_FOUND"}) from None
    except SpaceAccessDeniedError:
        raise HTTPException(403, detail={"code": "SPACE_MEMBER_REQUIRED"}) from None
    except SpaceKnowledgeError as exc:
        code = exc.code if exc.code in _CODES else "DEPENDENCY_UNAVAILABLE"
        raise HTTPException(_CODES[code], detail={"code": code}) from None
    if isinstance(result, KnowledgeBasePage):
        return asdict(result)
    response.status_code = 202 if result.accepted else 200
    return asdict(result.state)


@router.post("")
async def create(
    space_id: SpaceId,
    body: CreateKnowledgeBaseRequest,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SpaceKnowledgeServiceProtocol = Injected(SpaceKnowledgeServiceProtocol),
):
    return await _invoke(
        lambda: service.create(
            space_id=space_id,
            actor_id=user.staffId,
            request_id=body.request_id,
            name=body.name,
            description=body.description,
        ),
        response,
    )


@router.get("/{kb_id}")
async def get(
    space_id: SpaceId,
    kb_id: KnowledgeId,
    response: Response,
    user: AuthenticatedUser = Depends(get_current_user),
    service: SpaceKnowledgeServiceProtocol = Injected(SpaceKnowledgeServiceProtocol),
):
    return await _invoke(
        lambda: service.get(space_id=space_id, actor_id=user.staffId, kb_id=kb_id),
        response,
    )


@router.get("")
async def list_knowledge_bases(
    space_id: SpaceId,
    response: Response,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[
        str, Query(max_length=128, pattern=r"^[^\s\x00-\x1f\x7f/]*$")
    ] = "",
    user: AuthenticatedUser = Depends(get_current_user),
    service: SpaceKnowledgeServiceProtocol = Injected(SpaceKnowledgeServiceProtocol),
):
    return await _invoke(
        lambda: service.list_knowledge_bases(
            space_id=space_id, actor_id=user.staffId, page_size=page_size, cursor=cursor
        ),
        response,
    )
