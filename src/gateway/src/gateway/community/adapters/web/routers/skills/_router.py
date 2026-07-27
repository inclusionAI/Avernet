"""Skills group — catalog + a bot's installed skills (definition only).

The catalog lives at ``/openapi/v1/skills``; a bot's installed skills are a
sub-resource of the bot (``/openapi/v1/bots/{bot_id}/skills``). Handlers are
stubs; every route requires an authenticated user principal.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from gateway.community.adapters.web import require_identities
from gateway.community.adapters.web.contracts import (
    Deleted,
    Envelope,
    Page,
    PageParamsDep,
    requires_user_principal,
)
from gateway.community.spi.authn import Identities

from ._schemas import BotSkill, Skill, SkillDetail, SkillInstall

router = APIRouter(prefix="/openapi/v1", tags=["skills"])

_SEC = requires_user_principal()
IdentitiesDep = Annotated[Identities, Depends(require_identities)]


@router.get("/skills", response_model=Envelope[Page[Skill]], openapi_extra=_SEC)
async def list_skills(
    page: PageParamsDep, identities: IdentitiesDep, keyword: str | None = None
) -> Envelope[Page[Skill]]:
    """List the skill catalog (filter + paginate)."""
    raise NotImplementedError


@router.get(
    "/skills/{skill_id}", response_model=Envelope[SkillDetail], openapi_extra=_SEC
)
async def get_skill(skill_id: str, identities: IdentitiesDep) -> Envelope[SkillDetail]:
    """Get a skill's detail."""
    raise NotImplementedError


@router.get(
    "/bots/{bot_id}/skills",
    response_model=Envelope[list[BotSkill]],
    openapi_extra=_SEC,
)
async def list_bot_skills(
    bot_id: str, identities: IdentitiesDep
) -> Envelope[list[BotSkill]]:
    """List the skills installed on a bot."""
    raise NotImplementedError


@router.post(
    "/bots/{bot_id}/skills",
    status_code=201,
    response_model=Envelope[BotSkill],
    openapi_extra=_SEC,
)
async def install_bot_skill(
    bot_id: str, body: SkillInstall, identities: IdentitiesDep
) -> Envelope[BotSkill]:
    """Install a skill on a bot."""
    raise NotImplementedError


@router.delete(
    "/bots/{bot_id}/skills/{skill_id}",
    response_model=Envelope[Deleted],
    openapi_extra=_SEC,
)
async def remove_bot_skill(
    bot_id: str, skill_id: str, identities: IdentitiesDep
) -> Envelope[Deleted]:
    """Remove a skill from a bot."""
    raise NotImplementedError
