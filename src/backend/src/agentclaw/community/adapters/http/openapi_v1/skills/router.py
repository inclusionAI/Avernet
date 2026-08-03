"""Public lifecycle routes for Bot-owned Local Skills.

This router deliberately exposes only the six ratified Local Skill operations.
Git, Center, marketplace, and install semantics remain on their separate,
non-public surfaces.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    ErrorEnvelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.local_skill_query_service import (
    LocalSkillQueryServiceProtocol,
)
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.api.local_skill_state_service import (
    LocalSkillStateServiceProtocol,
)
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.core.skill_center.errors import LocalSkillInvalidPackageError
from agentclaw.community.di import Injected

from .schemas import Skill, SkillState, SkillUpload

router = APIRouter(prefix="/openapi/v1/bots/skills", tags=["skills"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]

def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(tag) for tag in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
        return [str(tag) for tag in parsed] if isinstance(parsed, list) else []
    return []


def _to_skill(record: dict[str, Any]) -> Skill:
    return Skill(
        skill_id=str(record["id"]),
        name=str(record["name"]),
        description=record.get("description"),
        category=record.get("category"),
        tags=_tags(record.get("tags")),
        active=bool(record["active"]),
        created_at=record.get("gmt_created"),
        updated_at=record.get("gmt_modified"),
    )


@router.get("", response_model=Envelope[Page[Skill]])
@envelope_errors
async def list_skills(
    page: PageParamsDep,
    principal: PrincipalDep,
    request: Request,
    bot_id: str = Query(..., description="Bot ID whose Local Skills are listed."),
    owner_entity_id: str | None = Query(
        default=None, description="Verified Bot owner locator."
    ),
    active: bool | None = Query(default=None),
    keyword: str | None = Query(default=None),
    query_service: LocalSkillQueryServiceProtocol = Injected(
        LocalSkillQueryServiceProtocol
    ),
) -> Envelope[Page[Skill]]:
    """List exact Bot-owned Local Skills from database desired state."""
    actor_id = caller_owner_id(principal)
    total, records = query_service.list_local_skills(
        bot_id=bot_id,
        owner_id=owner_entity_id or actor_id,
        actor_id=actor_id,
        page=page.page,
        page_size=page.page_size,
        active=active,
        keyword=keyword,
    )
    return page_envelope(total, [_to_skill(record) for record in records], request)


@router.get("/{skill_id}", response_model=Envelope[Skill])
@envelope_errors
async def get_skill(
    skill_id: str,
    principal: PrincipalDep,
    request: Request,
    query_service: LocalSkillQueryServiceProtocol = Injected(
        LocalSkillQueryServiceProtocol
    ),
) -> Envelope[Skill]:
    """Get public metadata for one Local Skill; the Skill ID selects its Bot."""
    record = query_service.get_local_skill(
        skill_id=skill_id, actor_id=caller_owner_id(principal)
    )
    return envelope(_to_skill(record), request)


@router.post(
    "/upload",
    status_code=201,
    response_model=Envelope[SkillUpload],
    responses={
        200: {
            "model": Envelope[SkillUpload],
            "description": "Existing Local Skill safely replaced.",
        },
        413: {
            "model": ErrorEnvelope,
            "description": "ZIP package exceeds an upload limit.",
            "content": {
                "application/json": {
                    "example": {
                        "code": 413101,
                        "message": "Skill package is too large",
                        "data": None,
                        "request_id": "",
                    }
                }
            },
        },
    },
)
@envelope_errors
async def upload_skill(
    principal: PrincipalDep,
    request: Request,
    response: Response,
    package: bytes = Body(..., media_type="application/zip"),
    bot_id: str = Query(..., description="Ready Bot that owns the Local Skill."),
    owner_entity_id: str | None = Query(
        default=None, description="Verified Bot owner locator."
    ),
    upload_service: LocalSkillUploadServiceProtocol = Injected(
        LocalSkillUploadServiceProtocol
    ),
) -> Envelope[SkillUpload]:
    """Create one inactive Local Skill from a complete raw ZIP package."""
    if (
        request.headers.get("content-type", "").split(";", 1)[0].lower()
        != "application/zip"
    ):
        raise LocalSkillInvalidPackageError()
    actor_id = caller_owner_id(principal)
    result = await upload_service.upload_local_skill(
        bot_id=bot_id,
        owner_id=owner_entity_id or actor_id,
        actor_id=actor_id,
        package=package,
    )
    operation = str(result["operation"])
    if operation == "updated":
        response.status_code = 200
    return envelope(
        SkillUpload(operation=operation, skill=_to_skill(result["skill"])),
        request,
        code=201000 if operation == "created" else 200000,
        message="Created" if operation == "created" else "OK",
    )


@router.post(
    "/{skill_id}/activate",
    response_model=Envelope[SkillState],
)
@envelope_errors
async def activate_skill(
    skill_id: str,
    principal: PrincipalDep,
    request: Request,
    state_service: LocalSkillStateServiceProtocol = Injected(
        LocalSkillStateServiceProtocol
    ),
) -> Envelope[SkillState]:
    """Activate one Bot-owned Local Skill and synchronously reconcile runtime."""
    result = await state_service.set_local_skill_active(
        skill_id=skill_id, actor_id=caller_owner_id(principal), active=True
    )
    return envelope(
        SkillState(skill=_to_skill(result), changed=bool(result["changed"])),
        request,
    )


@router.post(
    "/{skill_id}/deactivate",
    response_model=Envelope[SkillState],
)
@envelope_errors
async def deactivate_skill(
    skill_id: str,
    principal: PrincipalDep,
    request: Request,
    state_service: LocalSkillStateServiceProtocol = Injected(
        LocalSkillStateServiceProtocol
    ),
) -> Envelope[SkillState]:
    """Deactivate one Bot-owned Local Skill and synchronously reconcile runtime."""
    result = await state_service.set_local_skill_active(
        skill_id=skill_id, actor_id=caller_owner_id(principal), active=False
    )
    return envelope(
        SkillState(skill=_to_skill(result), changed=bool(result["changed"])),
        request,
    )


@router.delete("/{skill_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_skill(
    skill_id: str,
    principal: PrincipalDep,
    request: Request,
    delete_service: LocalSkillDeleteServiceProtocol = Injected(
        LocalSkillDeleteServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Delete one inactive Bot-owned Local Skill by deployment-wide ID."""
    await delete_service.delete_local_skill(
        skill_id=skill_id, actor_id=caller_owner_id(principal)
    )
    return envelope(Deleted(), request)
