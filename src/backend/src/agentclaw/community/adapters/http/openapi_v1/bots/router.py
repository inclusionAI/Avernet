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
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.clusters import (
    cluster_for_engine,
    validate_engine_cluster,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    created,
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    BotCreateSpec,
    complete_bot_authorization,
    create_bot_with_authorization,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    generate_bot_id,
)
from agentclaw.community.core.services.engine_config import EngineConfigService
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
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
    engine = d.get("active_engine") or ""
    return Bot(
        bot_id=d["bot_id"],
        bot_name=d.get("bot_name") or "",
        bot_desc=d.get("bot_desc") or "",
        engine=engine,
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
@envelope_errors
async def create_bot(
    body: BotCreate,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
    skill_set_factory: SkillSetServiceFactory = Injected(SkillSetServiceFactory),
):
    """Create a bot (201), or return 202 + a Passport iframe when authorization is needed.

    ``engine_options`` is accepted but not yet wired — the internal create path
    has no engine-options input; flagged for follow-up.
    """
    owner_id = caller_owner_id(principal)
    # The engine/cluster pair must obey the bijection (ANDC⟺teclaw, ACRA⟺else).
    validate_engine_cluster(body.engine, body.cluster_name)

    bot_id = generate_bot_id(owner_id, bot_repo)
    outcome = create_bot_with_authorization(
        user_id=owner_id,
        nick_name=owner_id,
        bot_id=bot_id,
        spec=BotCreateSpec(
            entity_id=owner_id,
            engine_type=body.engine,
            bot_name=body.bot_name,
            bot_desc=body.bot_desc,
            bot_type=body.bot_type,
        ),
        cookie=request.headers.get("cookie", ""),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
        skill_set_factory=skill_set_factory,
    )

    if isinstance(outcome, AuthPending):
        pending = accepted(
            BotAuthPending(bot_id=outcome.bot_id, iframe_url=outcome.iframe_url or ""),
            request,
        )
        return JSONResponse(status_code=202, content=pending.model_dump())

    return created(_to_bot(outcome.bot), request)


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
@envelope_errors
async def update_bot(
    bot_id: str,
    body: BotUpdate,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Bot]:
    """Update a bot's name/description (engine is fixed at creation).

    ``cluster_name`` is engine-derived and the engine is immutable, so it is not
    updatable here; ``engine_options`` is managed via the engine-config endpoints.
    Both are accepted for schema symmetry but do not drive this update.
    """
    owner_id = caller_owner_id(principal)
    bot = bot_service.update_bot(
        bot_id, owner_id, bot_name=body.bot_name, bot_desc=body.bot_desc
    )
    return envelope(_to_bot(bot), request)


@router.delete("/{bot_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_bot(
    bot_id: str,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Deleted]:
    """Delete a bot."""
    owner_id = caller_owner_id(principal)
    bot_service.delete_bot(bot_id, owner_id)
    return deleted_envelope(request)


@router.post("/{bot_id}/restart", response_model=Envelope[Bot])
@envelope_errors
async def restart_bot(
    bot_id: str,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Bot]:
    """Restart a bot (re-provision its device)."""
    owner_id = caller_owner_id(principal)
    bot = bot_service.restart_bot(bot_id, owner_id)
    return envelope(_to_bot(bot), request)


@router.get("/{bot_id}/auth-status", response_model=Envelope[BotAuthStatus])
@envelope_errors
async def get_bot_auth_status(
    bot_id: str,
    request: Request,
    principal: PrincipalDep,
    engine: str | None = None,
    cluster_name: str | None = None,
    bot_name: str | None = None,
    bot_desc: str | None = None,
    bot_type: str | None = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
) -> Envelope[BotAuthStatus]:
    """Poll Passport authorization; complete creation when ISSUED.

    On the async-create flow the bot is only actually created here (on ISSUED),
    so the caller must re-supply the attributes it created with — passed as
    optional query params and forwarded to completion. Without them the bot
    would be created with defaults (e.g. engine ``openclaw``) that contradict the
    Passport applied for at ``POST`` time, so callers on the 202 flow should
    always echo back ``engine``/``cluster_name``/``bot_name``/… here.
    """
    owner_id = caller_owner_id(principal)
    if engine is not None and cluster_name is not None:
        validate_engine_cluster(engine, cluster_name)
    result = complete_bot_authorization(
        user_id=owner_id,
        nick_name=owner_id,
        bot_id=bot_id,
        spec=BotCreateSpec(
            entity_id=owner_id,
            engine_type=engine or DEFAULT_ENGINE_TYPE,
            bot_name=bot_name,
            bot_desc=bot_desc,
            bot_type=bot_type,
        ),
        cookie=request.headers.get("cookie", ""),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
    )
    bot = _to_bot(result.bot) if result.bot else None
    return envelope(BotAuthStatus(status=result.status, bot=bot), request)


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


def _engine_config_target(bot: dict[str, Any]) -> tuple[str, str, str]:
    """Resolve (entity_id, entity_type, engine) for an engine-config call."""
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    entity_type = bot.get("entity_type") or "staff"
    engine = bot.get("active_engine") or DEFAULT_ENGINE_TYPE
    return entity_id, entity_type, engine


@router.get(
    "/{bot_id}/engine-config",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def get_bot_engine_config(
    bot_id: str,
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigService = Injected(EngineConfigService),
) -> Envelope[dict[str, Any]]:
    """Read a bot's engine configuration (free-form JSON)."""
    owner_id = caller_owner_id(principal)
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id, entity_type, engine = _engine_config_target(bot)
    data = await engine_config_service.read_bot_config(
        bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
        entity_type=entity_type, engine_type=engine,
    )
    return envelope(data, request)


@router.put(
    "/{bot_id}/engine-config",
    response_model=Envelope[dict[str, Any]],
)
@envelope_errors
async def update_bot_engine_config(
    bot_id: str,
    body: dict[str, Any],
    request: Request,
    principal: PrincipalDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigService = Injected(EngineConfigService),
) -> Envelope[dict[str, Any]]:
    """Write a bot's engine configuration (free-form JSON)."""
    owner_id = caller_owner_id(principal)
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id, entity_type, engine = _engine_config_target(bot)
    await engine_config_service.write_bot_config(
        bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
        entity_type=entity_type, engine_type=engine, config=body,
    )
    return envelope(body, request)
