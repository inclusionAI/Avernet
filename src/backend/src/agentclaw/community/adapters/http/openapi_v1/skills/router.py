"""Skills group — ``/openapi/v1/bots/skills``: catalog + a bot's installed
skills (definition only).

This component owns two resource families. A bot's installed skills are
bot-scoped and take ``{bot_id}`` as the first segment after the component, as
everywhere on this surface. The catalog is *not* bot-scoped, and a catalog
detail at ``/openapi/v1/bots/skills/{skill_id}`` would occupy the same slot as
``/openapi/v1/bots/skills/{bot_id}`` with a different meaning — two wildcards
at one depth, which no ordering rule can tell apart. So the catalog takes a
literal ``catalog`` segment, the same device the surface already uses for
``check-name`` and ``ceiling``.

Handlers are stubs; every route requires an authenticated user principal.
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

router = APIRouter(prefix="/openapi/v1/bots/skills", tags=["skills"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]

# Declaration order below is load-bearing. ``/catalog`` and
# ``/catalog/{skill_id}`` compete with ``/{bot_id}`` and ``/{bot_id}/{skill_id}``
# at the same depth, and FastAPI resolves first-registered — so the two catalog
# routes must stay above the two bot-scoped ones, or ``GET .../skills/catalog``
# starts meaning "the installed skills of the bot named ``catalog``". This is
# the same ordering contract ``openapi_v1/__init__.py`` documents at the mount
# level, applied within one router.


@router.get("/catalog", response_model=Envelope[Page[Skill]])
async def list_skills(
    page: PageParamsDep, principal: PrincipalDep, keyword: str | None = None
) -> Envelope[Page[Skill]]:
    """List the skill catalog (filter + paginate)."""
    raise NotImplementedError


@router.get("/catalog/{skill_id}", response_model=Envelope[SkillDetail])
async def get_skill(skill_id: str, principal: PrincipalDep) -> Envelope[SkillDetail]:
    """Get a skill's detail."""
    raise NotImplementedError


@router.get(
    "/{bot_id}",
    response_model=Envelope[list[BotSkill]],
)
async def list_bot_skills(
    bot_id: str, principal: PrincipalDep
) -> Envelope[list[BotSkill]]:
    """List the skills installed on a bot."""
    raise NotImplementedError


@router.post(
    "/{bot_id}",
    status_code=201,
    response_model=Envelope[BotSkill],
)
async def install_bot_skill(
    bot_id: str, body: SkillInstall, principal: PrincipalDep
) -> Envelope[BotSkill]:
    """Install a skill on a bot."""
    raise NotImplementedError


@router.delete(
    "/{bot_id}/{skill_id}",
    response_model=Envelope[Deleted],
)
async def remove_bot_skill(
    bot_id: str, skill_id: str, principal: PrincipalDep
) -> Envelope[Deleted]:
    """Remove a skill from a bot."""
    raise NotImplementedError
