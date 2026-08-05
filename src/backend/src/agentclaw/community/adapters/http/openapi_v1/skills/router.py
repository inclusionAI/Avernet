"""Public contract stubs for Bot-owned Local Skill lifecycle operations.

The public surface deliberately has no catalog, marketplace, or installation
relationship. Every operation requires an authenticated principal; Track B
implementation slices wire these definitions to the domain services.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query

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

from .schemas import Skill, SkillState, SkillUpload

router = APIRouter(prefix="/openapi/v1/bots/skills", tags=["skills"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get("", response_model=Envelope[Page[Skill]])
async def list_skills(
    page: PageParamsDep,
    principal: PrincipalDep,
    bot_id: str = Query(..., description="Bot ID whose Local Skills are listed."),
    owner_entity_id: str | None = Query(
        default=None, description="Verified Bot owner locator."
    ),
    active: bool | None = Query(
        default=None, description="Filter by desired Active state."
    ),
    keyword: str | None = Query(
        default=None, description="Case-insensitive name or description filter."
    ),
) -> Envelope[Page[Skill]]:
    """List one Bot's Local Skills from persisted desired state."""
    raise NotImplementedError


@router.get("/{skill_id}", response_model=Envelope[Skill])
async def get_skill(skill_id: str, principal: PrincipalDep) -> Envelope[Skill]:
    """Get public metadata for one Local Skill selected by its Skill ID."""
    raise NotImplementedError


@router.post(
    "/upload",
    status_code=201,
    response_model=Envelope[SkillUpload],
    responses={
        200: {
            "model": Envelope[SkillUpload],
            "description": "Same-name Local Skill replaced successfully.",
        },
        413: {
            "model": ErrorEnvelope,
            "description": "ZIP package exceeds an upload limit.",
        },
    },
)
async def upload_skill(
    content: Annotated[bytes, Body(media_type="application/zip")],
    principal: PrincipalDep,
    bot_id: str = Query(..., description="Bot that owns and stores the Local Skill."),
    owner_entity_id: str | None = Query(
        default=None, description="Verified Bot owner locator."
    ),
) -> Envelope[SkillUpload]:
    """Create or safely replace one Local Skill from a raw ZIP body."""
    raise NotImplementedError


@router.post(
    "/{skill_id}/activate",
    response_model=Envelope[SkillState],
)
async def activate_skill(
    skill_id: str, principal: PrincipalDep
) -> Envelope[SkillState]:
    """Set one Local Skill's desired state to Active."""
    raise NotImplementedError


@router.post(
    "/{skill_id}/deactivate",
    response_model=Envelope[SkillState],
)
async def deactivate_skill(
    skill_id: str, principal: PrincipalDep
) -> Envelope[SkillState]:
    """Set one Local Skill's desired state to Inactive."""
    raise NotImplementedError


@router.delete("/{skill_id}", response_model=Envelope[Deleted])
async def delete_skill(skill_id: str, principal: PrincipalDep) -> Envelope[Deleted]:
    """Delete one Inactive Local Skill selected by its Skill ID."""
    raise NotImplementedError
