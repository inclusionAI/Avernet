"""Skills group — catalog + a bot's installed skills (definition only).

The catalog lives at ``/openapi/v1/skills``; a bot's installed skills are a
sub-resource of the bot (``/openapi/v1/bots/{bot_id}/skills``). Handlers are
stubs; every route requires an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal

from .schemas import BotSkill, Skill, SkillDetail, SkillInstall

router = APIRouter(prefix="/openapi/v1/bots", tags=["skills"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.get("/skills", response_model=Envelope[Page[Skill]])
async def list_skills(
    page: PageParamsDep, principal: PrincipalDep, keyword: str | None = None
) -> Envelope[Page[Skill]]:
    """List the skill catalog (filter + paginate)."""
    raise NotImplementedError


@router.get("/skills/{skill_id}", response_model=Envelope[SkillDetail])
async def get_skill(skill_id: str, principal: PrincipalDep) -> Envelope[SkillDetail]:
    """Get a skill's detail."""
    raise NotImplementedError


@router.get(
    "/{bot_id}/skills",
    response_model=Envelope[list[BotSkill]],
)
async def list_bot_skills(
    bot_id: str, principal: PrincipalDep
) -> Envelope[list[BotSkill]]:
    """List the skills installed on a bot."""
    raise NotImplementedError


@router.post(
    "/{bot_id}/skills",
    status_code=201,
    response_model=Envelope[BotSkill],
)
async def install_bot_skill(
    bot_id: str, body: SkillInstall, principal: PrincipalDep
) -> Envelope[BotSkill]:
    """Install a skill on a bot."""
    raise NotImplementedError


@router.delete(
    "/{bot_id}/skills/{skill_id}",
    response_model=Envelope[Deleted],
)
async def remove_bot_skill(
    bot_id: str, skill_id: str, principal: PrincipalDep
) -> Envelope[Deleted]:
    """Remove a skill from a bot."""
    raise NotImplementedError
