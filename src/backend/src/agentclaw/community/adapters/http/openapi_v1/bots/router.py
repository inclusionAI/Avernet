"""Bots group — ``/openapi/v1/bots`` endpoints.

Public handlers that delegate to the existing internal bot services and wrap the
result in the standard :class:`Envelope` / :class:`Page` contracts. Identity is
the end user the request names in ``?user_id=`` (owner-scoping, via
``UserIdDep``) — on 12 of the 13 operations here; ``check-name`` asks a
tenant-wide question and takes none. The request tenant is bound by
``AvernetTenantMiddleware``
before the handler runs, so every service read/write is already tenant-scoped by
the Track A guard. Services are obtained with ``Injected`` exactly as the
internal router does.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    Deleted,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.clusters import (
    ClusterName,
    cluster_for_engine,
    validate_engine_cluster,
)
from agentclaw.community.adapters.http.openapi_v1.errors import UnsupportedEngineError
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
    require_granted_bot,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    created,
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    AuthStatus,
    BotCreateSpec,
    complete_bot_authorization,
    create_bot_with_authorization,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotOperationNotAllowedError,
    generate_bot_id,
    validate_bot_name,
)
from agentclaw.community.api.engine_config_service import EngineConfigServiceProtocol
from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,
    _get_engine_types,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportPlugin

from .schemas import (
    Bot,
    BotAuthPending,
    BotAuthStatus,
    BotCreate,
    BotStatus,
    BotType,
    BotUpdate,
    Ceiling,
    Passport,
)

logger = get_logger()

#: The bot authorization for an application caller, on the Mode A1 operations
#: of this group.
#:
#: Declared per route here, unlike the four groups that are wholly A1 and get it
#: at ``include_router``. This group is mixed: it also holds the bots listing
#: (Mode B), the ceiling (C), the name check (OPEN) and bot creation (refused),
#: none of which names a bot — and on those the check would refuse an
#: application outright rather than authorize it. ``admission.py`` is the
#: authority on which route is which; ``test_principal_seam.py`` fails if a
#: declaration and a mode disagree.
_GRANT_CHECKED = [Depends(require_granted_bot)]

router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"])


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


def _auth_status_error(status: str, request: Request) -> JSONResponse:
    """400 envelope for a terminal (non-PENDING/ISSUED) authorization state.

    The state itself is kept in ``data`` so the caller can tell *why* it failed,
    while the 400 + ``400000`` code stop a rejected creation from reading as a
    success to clients that key off the envelope code.
    """
    body = envelope(
        BotAuthStatus(status=status, bot=None),
        request,
        code=400 * 1000,
        message="Authorization did not complete",
    )
    return JSONResponse(status_code=400, content=body.model_dump())


def _reject_unowned_lifecycle(bot: dict[str, Any], *, deleting: bool = False) -> None:
    """Refuse lifecycle operations this surface does not own (→ 409).

    Desktop bots have a dedicated service and their own internal namespace
    (``/api/desktop/bots``): deletion there also destroys the BaaS container and
    approves the destruction publish, and restart re-provisions through the same
    path. The generic ``BotService`` methods this router calls do none of that,
    so routing a desktop bot through them would soft-delete the local row and
    leave its container running.

    This surface already refuses to *create* desktop bots, so refusing to manage
    their lifecycle keeps one consistent line: ``/openapi/v1/bots`` does not
    handle desktop bots at all. Delegating instead would mean taking on the
    desktop service as a public dependency — a wider change than this surface
    should make on its own.
    """
    bot_type = bot.get("bot_type") or ""
    if bot_type == "desktop":
        raise BotOperationNotAllowedError(
            "desktop bots are managed by the desktop service"
        )
    # Service bots additionally have a publish lifecycle. Deleting one goes
    # through BotPublishService.delete_service_bot, which refuses unless the
    # publication is a deletable draft with no successful publish, and destroys
    # the verification histories first. Generic delete_bot does none of that, so
    # it would remove the source bot, Passport and device while successful
    # publication records and verification resources survive.
    #
    # Only deletion: reads and restart do not touch the publication.
    if deleting and bot_type == "service":
        raise BotOperationNotAllowedError(
            "service bots are deleted through their publish lifecycle"
        )


def _bcn_auth_headers(request: Request) -> dict[str, str]:
    """The caller's bearer token, for the downstream BCN identity check.

    ``BotService._sync_bot_to_bcn`` forwards ``request_headers`` to
    ``BcnService.onboard_bot``, which extracts ``Cookie`` / ``Authorization`` to
    identify the caller. Passing nothing makes that call unauthenticated, and the
    failure is swallowed (warning only) — so a rename would answer 200 while the
    coordination-network name stayed stale, on every public update.

    Only ``Authorization`` is forwarded. The ``Cookie`` half stays out on
    purpose: a browser session credential has no business below the adapter
    boundary, and this surface's callers are registered tenants presenting a
    bearer token, not browser sessions. Returns ``{}`` when the caller sent no
    Authorization header, which is the same "no credential" state as before.
    """
    authorization = request.headers.get("Authorization")
    return {"Authorization": authorization} if authorization else {}


def _sync_passport_identity(
    passport_plugin: PassportPlugin,
    *,
    bot_id: str,
    owner_id: str,
    bot_name: str | None,
    bot_desc: str | None,
    engine_type: str | None,
) -> None:
    """Push renamed identity metadata to the Passport (best-effort).

    Mirrors the internal update route: metadata only, no MCP/CLI resource scope,
    and a failure is logged rather than failing the update the caller already
    succeeded in making.
    """
    try:
        passport_plugin.update_passport(
            bot_id=bot_id,
            user_id=owner_id,
            bot_name=bot_name,
            bot_desc=bot_desc,
            engine_type=engine_type or DEFAULT_ENGINE_TYPE,
        )
    except Exception as e:  # noqa: BLE001 — must not fail an applied update
        logger.warning(
            "[openapi_v1.update_bot] passport sync failed for bot %s: %s", bot_id, e
        )


@router.post(
    "",
    status_code=201,
    response_model=Envelope[Bot],
    responses={
        **USER_SCOPED_403,
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
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
    skill_set_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
):
    """Create a bot (201), or return 202 + a Passport iframe when authorization is needed.

    Engine-specific inputs belong in ``BotCreateSpec.extra_properties``, but
    nothing downstream reads that bag yet, so the request model does not expose
    an ``engine_options`` field for it — see :class:`BotCreate`.
    """
    # Validate the engine against the configured registry FIRST: the cluster rule
    # below treats every non-teclaw value as ACRA, so an unknown engine would
    # otherwise sail through, allocate an id, apply for a Passport, and only fail
    # later at device provisioning — with those side effects already committed.
    if body.engine not in _get_engine_types():
        raise UnsupportedEngineError(body.engine)
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
            bot_type=body.bot_type,
            bot_name=body.bot_name,
            bot_desc=body.bot_desc,
        ),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
        skill_set_factory=skill_set_factory,
    )

    if isinstance(outcome, AuthPending):
        # Forward BOTH handles — Passport may return either, and dropping one
        # can leave the caller with no way to complete authorization.
        pending = accepted(
            BotAuthPending(
                bot_id=outcome.bot_id,
                iframe_url=outcome.iframe_url or "",
                redirect_url=outcome.redirect_url or "",
            ),
            request,
        )
        return JSONResponse(status_code=202, content=pending.model_dump())

    return created(_to_bot(outcome.bot), request)


@router.get("", response_model=Envelope[Page[Bot]], responses=USER_SCOPED_403)
@envelope_errors
async def list_bots(
    request: Request,
    page_params: PageParamsDep,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    keyword: str | None = None,
    engine: str | None = None,
    status: str | None = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Page[Bot]]:
    """List the user's bots (filter + paginate), narrowed to what may be reached.

    For a human caller this is their own bots, unfiltered — unchanged.

    For an **application** the result is narrowed to the bots that user granted
    it. Filtering here rather than in the service keeps the narrowing beside the
    thing it protects, and it is applied **before** paginating: filtering a page
    after the fact would return short pages and, worse, let a caller infer how
    many bots it was *not* granted from the gaps. The count reports the narrowed
    set for the same reason.

    An application granted nothing gets an empty page, not an error: naming no
    bot, this operation has nothing to mask.

    Note this listing can never show a bot the user does not own, for an
    application any more than for the user — it is owner-scoped underneath. The
    complete view of what an application may reach, including bots delegated by
    a collaborator, is ``GET /openapi/v1/bots/authorized``.
    """
    granted = caller.granted_bot_ids()
    if granted is not None and not granted:
        # Granted nothing: answer without asking the service for a page it
        # would have to discard entirely.
        return page(0, [], request)
    result = bot_service.list_bots_by_conditions(
        owner_id=owner_id,
        bot_name=keyword,
        engine=engine,
        status=status,
        page=page_params.page,
        page_size=page_params.page_size,
        bot_ids=sorted(granted) if granted is not None else None,
    )
    items = [_to_bot(b) for b in result["items"]]
    return page(result["total"], items, request)


@router.get(
    "/check-name",
    response_model=Envelope[NameCheck],
    # Authenticated, but not user-scoped. Declared on the route rather than
    # inherited from ``build_public_router`` so the guard is visible where the
    # operation is, and so ``test_public_routes_require_principal`` can see it:
    # that test walks each route's own dependant, which a group-level
    # dependency does not appear in.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def check_bot_name(
    name: str,
    request: Request,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[NameCheck]:
    """Check whether a bot name is available (within the caller's tenant).

    Applies the same rule create and update do, because "available" has to mean
    "you could create this". ``check_bot_name_exists`` only does a repository
    lookup — it answers ``False`` for a blank or ``@``-bearing name, which would
    report a name as free that the very next request would reject (400).
    Rejecting here instead keeps one answer across the three endpoints.

    The echoed ``name`` is the trimmed form actually checked, so a caller that
    sends ``" Foo "`` sees which string the availability applies to.
    """
    # No ``user_id``: this operation has no user dimension to scope by. Name
    # uniqueness is checked across the whole tenant — ``check_bot_name_exists``
    # takes only the name.
    # An authenticated caller is still required — ``_PUBLIC_AUTH`` in
    # ``openapi_v1/__init__.py`` — it just has no user-shaped answer to give,
    # so asking the caller to name one would be asking for a value this handler
    # cannot use. See "Naming the end user" there.
    checked = validate_bot_name(name)
    exists = bot_service.check_bot_name_exists(checked)
    return envelope(NameCheck(name=checked, exists=exists), request)


@router.get("/ceiling", response_model=Envelope[Ceiling], responses=USER_SCOPED_403)
@envelope_errors
async def get_bots_ceiling(
    request: Request,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Ceiling]:
    """Get the named user's bot-creation quota ceiling.

    Names no bot, so there is no grant to check against one — but the answer is
    still *about a person's account*, and a stranger application must not be
    able to read it by naming a user id. So it is gated on the application
    holding **at least one** live delegation from that user: proof of a
    relationship, which is the closest thing this operation has to a scope.

    An application with no delegation from them is answered as if the user were
    not there. It learns nothing it did not already know.

    Resolved through the same method creation enforces, not
    ``PolicyService.get_bots_ceiling`` directly: that one falls back to its own
    hardcoded default of 5, while creation falls back to the configured
    ``max_devices_per_entity``. Reading it directly would advertise 5 to a caller
    whose deployment allows (or rejects at) a different number.
    """
    granted = caller.granted_bot_ids()
    if granted is not None and not granted:
        raise BotNotFoundError(f"no authorization from user {owner_id}")
    ceiling = bot_service.get_bots_ceiling_for_owner(owner_id)
    return envelope(Ceiling(ceiling=ceiling), request)


@router.get(
    "/{bot_id}",
    response_model=Envelope[Bot],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def get_bot(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Bot]:
    """Get a bot's details."""
    bot = bot_service.get_bot(bot_id, owner_id)
    return envelope(_to_bot(bot), request)


@router.put(
    "/{bot_id}",
    response_model=Envelope[Bot],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def update_bot(
    bot_id: str,
    body: BotUpdate,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
) -> Envelope[Bot]:
    """Update a bot's name/description (engine is fixed at creation).

    ``cluster_name`` is engine-derived and the engine is immutable, so it is not
    updatable here; ``engine_options`` is managed via the engine-config
    endpoints. Neither is accepted — see :class:`BotUpdate`.
    """
    # Same name rule as create and the internal update route — otherwise this
    # surface could persist names the rest of the lifecycle rejects.
    bot_name = validate_bot_name(body.bot_name) if body.bot_name is not None else None
    bot = bot_service.update_bot(
        bot_id,
        owner_id,
        bot_name=bot_name,
        bot_desc=body.bot_desc,
# BCN sync re-enabled: new bots get a globally-unique bot_id
        # (generate_bot_id), so (bot_id, owner_workno) no longer collides
        # across tenants for them. Legacy "default" bots (pre-retirement,
        # unmigrated) retain residual cross-tenant risk on this identifier —
        # tracked as a follow-up; acceptable here because the F49 stopgap is
        # lifted for the common (new-bot) path. ``update_bot`` defaults
        # ``sync_to_bcn=True`` — no longer forced False on this surface.
        # Bearer token only — see _bcn_auth_headers. Kept so re-enabling the
        # sync does not silently reintroduce the unauthenticated call F37 fixed.
        request_headers=_bcn_auth_headers(request),
    )
    # Identity metadata lives in the Passport too; leaving it stale would make
    # Passport queries disagree with the bot API. Presence, not truthiness —
    # clearing a description with "" is a real metadata change that gets
    # persisted, so it has to reach the Passport as well.
    if bot_name is not None or body.bot_desc is not None:
        _sync_passport_identity(
            passport_plugin,
            bot_id=bot_id,
            owner_id=owner_id,
            bot_name=bot_name,
            bot_desc=body.bot_desc,
            engine_type=bot.get("active_engine"),
        )
    return envelope(_to_bot(bot), request)


@router.delete(
    "/{bot_id}",
    response_model=Envelope[Deleted],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def delete_bot(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Deleted]:
    """Delete a bot. See :func:`_reject_unowned_lifecycle` for what is refused."""
    _reject_unowned_lifecycle(bot_service.get_bot(bot_id, owner_id), deleting=True)
    bot_service.delete_bot(bot_id, owner_id)
    return deleted_envelope(request)


@router.post(
    "/{bot_id}/restart",
    response_model=Envelope[Bot],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def restart_bot(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Bot]:
    """Restart a bot. Desktop bots are rejected — see :func:`_reject_unowned_lifecycle`."""
    _reject_unowned_lifecycle(bot_service.get_bot(bot_id, owner_id))
    bot = bot_service.restart_bot(bot_id, owner_id)
    return envelope(_to_bot(bot), request)


@router.get(
    "/{bot_id}/auth-status",
    response_model=Envelope[BotAuthStatus],
    responses={
        **USER_SCOPED_403,
        # This route's 400 is the one documented exception to the surface-wide
        # ErrorEnvelope (whose ``data`` is null): a terminal authorization state
        # is reported as a failure, but the state itself is the actionable part,
        # so it stays in ``data``. Declared here so generated clients
        # deserialize it against the model it actually returns.
        400: {
            "model": Envelope[BotAuthStatus],
            "description": "Authorization did not complete; `data.status` "
            "carries the terminal state (e.g. REJECTED, EXPIRED)",
        },
    },
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def get_bot_auth_status(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    engine: str | None = None,
    # Enum, not a bare str: validate_engine_cluster accepts only ACRA/ANDC,
    # so a plain string would let a generated client compile
    # ``cluster_name=foo`` that the server always rejects — the same
    # contract/behaviour gap as F29/F35/F41. Create already models it this way.
    cluster_name: ClusterName | None = None,
    bot_name: str | None = None,
    bot_desc: str | None = None,
    bot_type: BotType | None = None,
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

    Because this is where the record is actually inserted, every restriction
    ``POST`` enforces is re-applied to the echoed-back values: the same engine
    registry check, the same engine/cluster bijection, and the same
    personal|service restriction on ``bot_type``. Otherwise the completion path
    would be a way to create exactly the bots ``POST`` rejects.
    """
    # Validate against the engine completion will actually use, not against the
    # query param: omitting ``engine`` does not mean "no engine", it means the
    # default one. Checking only when ``engine`` was supplied let
    # ``?cluster_name=ANDC`` alone through, and the bot was then provisioned on
    # the ACRA default — a success response contradicting the request.
    effective_engine = engine if engine is not None else DEFAULT_ENGINE_TYPE
    # Check the engine completion will actually use, supplied or defaulted. A
    # deployment whose ENGINE_TYPES excludes openclaw would otherwise create a
    # bot on the default engine anyway — and since creation now persists the
    # configured registry, that bot's active_engine would be absent from its own
    # enabled-engine list. Runs before Passport is queried, so nothing external
    # happens for a request that cannot succeed.
    if effective_engine not in _get_engine_types():
        raise UnsupportedEngineError(effective_engine)
    if cluster_name is not None:
        validate_engine_cluster(effective_engine, cluster_name)
    result = complete_bot_authorization(
        user_id=owner_id,
        nick_name=owner_id,
        bot_id=bot_id,
        spec=BotCreateSpec(
            entity_id=owner_id,
            engine_type=engine or DEFAULT_ENGINE_TYPE,
            bot_type=bot_type or "personal",
            bot_name=bot_name,
            bot_desc=bot_desc,
        ),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
    )
    # PENDING (still waiting) and ISSUED (done) are successful outcomes of the
    # poll. Any other state — REJECTED, EXPIRED, anything the passport service
    # adds later — is a terminal failure: reporting it as 200/OK would let a
    # client that keys off the envelope code treat a rejected creation as
    # successful, which is how the internal surface treats it too (400).
    if result.status not in (AuthStatus.PENDING, AuthStatus.ISSUED):
        return _auth_status_error(result.status, request)

    bot = _to_bot(result.bot) if result.bot else None
    return envelope(BotAuthStatus(status=result.status, bot=bot), request)


@router.get(
    "/{bot_id}/status",
    response_model=Envelope[BotStatus],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def get_bot_status(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[BotStatus]:
    """Get a bot's runtime / device readiness."""
    bot = bot_service.get_bot(bot_id, owner_id)
    binding = bot.get("device_binding") or {}
    return envelope(
        BotStatus(
            status=bot.get("status") or "",
            # Shared policy: an application bot is not ready until its repo
            # checkout reports SUCCEEDED, so ACTIVE alone is not enough.
            is_ready=is_bot_ready(bot),
            device_id=binding.get("device_id") or bot.get("device_id"),
        ),
        request,
    )


@router.get(
    "/{bot_id}/passport",
    response_model=Envelope[Passport],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def get_bot_passport(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
) -> Envelope[Passport]:
    """Get a bot's Agent Passport."""
    bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard (raises 404)
    info = passport_plugin.query_agent_passport(bot_id=bot_id, owner_workno=owner_id)
    # Either identifier means "a passport exists". Which one is populated is a
    # provider detail: the local plugin issues an ``agent_id`` and leaves
    # ``agent_code`` null, so keying on ``agent_code`` alone made every locally
    # created bot 404 here despite the plugin returning its passport.
    info = info or {}
    passport_id = info.get("agent_code") or info.get("agent_id")
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
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def get_bot_engine_config(
    bot_id: str,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    """Read a bot's engine configuration (free-form JSON)."""
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
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED,
)
@envelope_errors
async def update_bot_engine_config(
    bot_id: str,
    body: dict[str, Any],
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    engine_config_service: EngineConfigServiceProtocol = Injected(
        EngineConfigServiceProtocol
    ),
) -> Envelope[dict[str, Any]]:
    """Write a bot's engine configuration (free-form JSON)."""
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id, entity_type, engine = _engine_config_target(bot)
    await engine_config_service.write_bot_config(
        bot_id=bot_id, owner_id=owner_id, entity_id=entity_id,
        entity_type=entity_type, engine_type=engine, config=body,
    )
    return envelope(body, request)
