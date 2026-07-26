"""Bots group — ``/openapi/v1/bots`` endpoints (definition only).

Handlers are stubs: they exist so FastAPI generates the OpenAPI contract; a
later pass wires them to services. The auth *requirement* is the gateway's
concern — it resolves the strategy from its route-security table and annotates
the served doc — so these routes carry no ``x-avernet-security`` marker.
``require_principal`` supplies the caller identity for the handler to make its
own **authorization** decision.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal

from .schemas import (
    Bot,
    BotAuthPending,
    BotAuthStatus,
    BotCreate,
    BotStatus,
    BotUpdate,
    Ceiling,
    Passport,
)

router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.post(
    "",
    status_code=201,
    response_model=Envelope[Bot],
    responses={
        202: {
            "model": Envelope[BotAuthPending],
            "description": "Needs user authorization",
        }
    },
)
async def create_bot(body: BotCreate, principal: PrincipalDep) -> Envelope[Bot]:
    """Create a bot (201), or return 202 + a Passport iframe when authorization is needed."""
    raise NotImplementedError


@router.get("", response_model=Envelope[Page[Bot]])
async def list_bots(
    page: PageParamsDep,
    principal: PrincipalDep,
    keyword: str | None = None,
    engine: str | None = None,
    status: str | None = None,
) -> Envelope[Page[Bot]]:
    """List the caller's bots (filter + paginate)."""
    raise NotImplementedError


@router.get("/check-name", response_model=Envelope[NameCheck])
async def check_bot_name(name: str, principal: PrincipalDep) -> Envelope[NameCheck]:
    """Check whether a bot name is available."""
    raise NotImplementedError


@router.get("/ceiling", response_model=Envelope[Ceiling])
async def get_bots_ceiling(principal: PrincipalDep) -> Envelope[Ceiling]:
    """Get the caller's bot-creation quota ceiling."""
    raise NotImplementedError


@router.get("/{bot_id}", response_model=Envelope[Bot])
async def get_bot(bot_id: str, principal: PrincipalDep) -> Envelope[Bot]:
    """Get a bot's details."""
    raise NotImplementedError


@router.put("/{bot_id}", response_model=Envelope[Bot])
async def update_bot(
    bot_id: str, body: BotUpdate, principal: PrincipalDep
) -> Envelope[Bot]:
    """Update a bot (engine is immutable)."""
    raise NotImplementedError


@router.delete("/{bot_id}", response_model=Envelope[Deleted])
async def delete_bot(bot_id: str, principal: PrincipalDep) -> Envelope[Deleted]:
    """Delete a bot."""
    raise NotImplementedError


@router.post("/{bot_id}/restart", response_model=Envelope[Bot])
async def restart_bot(bot_id: str, principal: PrincipalDep) -> Envelope[Bot]:
    """Restart a bot (re-provision its device)."""
    raise NotImplementedError


@router.get("/{bot_id}/auth-status", response_model=Envelope[BotAuthStatus])
async def get_bot_auth_status(
    bot_id: str, principal: PrincipalDep
) -> Envelope[BotAuthStatus]:
    """Poll Passport authorization; completes creation when ISSUED."""
    raise NotImplementedError


@router.get("/{bot_id}/status", response_model=Envelope[BotStatus])
async def get_bot_status(bot_id: str, principal: PrincipalDep) -> Envelope[BotStatus]:
    """Get a bot's runtime / device readiness."""
    raise NotImplementedError


@router.get("/{bot_id}/passport", response_model=Envelope[Passport])
async def get_bot_passport(bot_id: str, principal: PrincipalDep) -> Envelope[Passport]:
    """Get a bot's Agent Passport."""
    raise NotImplementedError


@router.get(
    "/{bot_id}/engine-config",
    response_model=Envelope[dict[str, Any]],
)
async def get_bot_engine_config(
    bot_id: str, principal: PrincipalDep
) -> Envelope[dict[str, Any]]:
    """Read a bot's engine configuration (free-form JSON)."""
    raise NotImplementedError


@router.put(
    "/{bot_id}/engine-config",
    response_model=Envelope[dict[str, Any]],
)
async def update_bot_engine_config(
    bot_id: str, body: dict[str, Any], principal: PrincipalDep
) -> Envelope[dict[str, Any]]:
    """Write a bot's engine configuration (free-form JSON)."""
    raise NotImplementedError
