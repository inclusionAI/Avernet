"""Bots group — ``/openapi/v1/bots`` endpoints.

Public handlers that delegate to the existing internal bot services and wrap the
result in the standard :class:`Envelope` / :class:`Page` contracts. Identity is
the caller resolved from ``require_principal`` (owner-scoping, via
``caller_owner_id``); the request tenant is bound by ``AvernetTenantMiddleware``
before the handler runs, so every service read/write is already tenant-scoped by
the Track A guard. Services are obtained with ``Injected`` exactly as the
internal router does.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.clusters import cluster_for_engine
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotNotFoundError
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.passport import PassportPlugin

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


def _to_bot(d: dict[str, Any]) -> Bot:
    """Adapt an internal bot ``to_dict()`` record to the public ``Bot`` schema."""
    engine = d.get("active_engine")
    return Bot(
        bot_id=d["bot_id"],
        bot_name=d.get("bot_name") or "",
        bot_desc=d.get("bot_desc") or "",
        engine=engine or "",
        cluster_name=cluster_for_engine(engine),
        bot_type=d.get("bot_type") or "",
        status=d.get("status") or "",
        owner_entity_id=d.get("owner_id") or "",
    )


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
async def create_bot(body: BotCreate, request: Request, principal: PrincipalDep):
    """Create a bot (201), or return 202 + a Passport iframe when authorization is needed."""
    raise NotImplementedError


@router.get("", response_model=Envelope[Page[Bot]])
@envelope_errors
async def list_bots(
    request: Request,
    page_params: PageParamsDep,
    principal: PrincipalDep,
    keyword: str | None = None,
    engine: str | None = None,
    status: str | None = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Page[Bot]]:
    """List the caller's bots (filter + paginate)."""
    owner_id = caller_owner_id(principal)
    result = bot_service.list_bots_by_conditions(
        owner_id=owner_id,
        bot_name=keyword,
        engine=engine,
        status=status,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    items = [_to_bot(b) for b in result["items"]]
    return page(result["total"], items, request)


@router.get("/check-name", response_model=Envelope[NameCheck])
@envelope_errors
async def check_bot_name(
    name: str,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[NameCheck]:
    """Check whether a bot name is available (within the caller's tenant)."""
    caller_owner_id(principal)  # require an authenticated caller
    exists = bot_service.check_bot_name_exists(name)
    return envelope(NameCheck(name=name, exists=exists), request)


@router.get("/ceiling", response_model=Envelope[Ceiling])
@envelope_errors
async def get_bots_ceiling(
    request: Request,
    principal: PrincipalDep,
    policy_service: PolicyServiceProtocol = Injected(PolicyServiceProtocol),
) -> Envelope[Ceiling]:
    """Get the caller's bot-creation quota ceiling."""
    owner_id = caller_owner_id(principal)
    ceiling = policy_service.get_bots_ceiling(entity_id=owner_id)
    return envelope(Ceiling(ceiling=ceiling), request)


@router.get("/{bot_id}", response_model=Envelope[Bot])
@envelope_errors
async def get_bot(
    bot_id: str,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Bot]:
    """Get a bot's details."""
    owner_id = caller_owner_id(principal)
    bot = bot_service.get_bot(bot_id, owner_id)
    return envelope(_to_bot(bot), request)


@router.put("/{bot_id}", response_model=Envelope[Bot])
async def update_bot(
    bot_id: str, body: BotUpdate, request: Request, principal: PrincipalDep
):
    """Update a bot (engine is immutable)."""
    raise NotImplementedError


@router.delete("/{bot_id}", response_model=Envelope[Deleted])
async def delete_bot(bot_id: str, request: Request, principal: PrincipalDep):
    """Delete a bot."""
    raise NotImplementedError


@router.post("/{bot_id}/restart", response_model=Envelope[Bot])
async def restart_bot(bot_id: str, request: Request, principal: PrincipalDep):
    """Restart a bot (re-provision its device)."""
    raise NotImplementedError


@router.get("/{bot_id}/auth-status", response_model=Envelope[BotAuthStatus])
async def get_bot_auth_status(bot_id: str, request: Request, principal: PrincipalDep):
    """Poll Passport authorization; completes creation when ISSUED."""
    raise NotImplementedError


@router.get("/{bot_id}/status", response_model=Envelope[BotStatus])
@envelope_errors
async def get_bot_status(
    bot_id: str,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[BotStatus]:
    """Get a bot's runtime / device readiness."""
    owner_id = caller_owner_id(principal)
    bot = bot_service.get_bot(bot_id, owner_id)
    binding = bot.get("device_binding") or {}
    status = bot.get("status") or ""
    return envelope(
        BotStatus(
            status=status,
            is_ready=status == "ACTIVE",
            device_id=binding.get("device_id") or bot.get("device_id"),
        ),
        request,
    )


@router.get("/{bot_id}/passport", response_model=Envelope[Passport])
@envelope_errors
async def get_bot_passport(
    bot_id: str,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
) -> Envelope[Passport]:
    """Get a bot's Agent Passport."""
    owner_id = caller_owner_id(principal)
    bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard (raises 404)
    info = passport_plugin.query_agent_passport(bot_id=bot_id, owner_workno=owner_id)
    passport_id = (info or {}).get("agent_code")
    if not passport_id:
        # No passport issued for this bot yet — a missing sub-resource is a 404.
        raise BotNotFoundError(f"passport not found: {bot_id}")
    return envelope(Passport(bot_id=bot_id, passport_id=passport_id), request)


@router.get(
    "/{bot_id}/engine-config",
    response_model=Envelope[dict[str, Any]],
)
async def get_bot_engine_config(
    bot_id: str, request: Request, principal: PrincipalDep
):
    """Read a bot's engine configuration (free-form JSON)."""
    raise NotImplementedError


@router.put(
    "/{bot_id}/engine-config",
    response_model=Envelope[dict[str, Any]],
)
async def update_bot_engine_config(
    bot_id: str, body: dict[str, Any], request: Request, principal: PrincipalDep
):
    """Write a bot's engine configuration (free-form JSON)."""
    raise NotImplementedError
