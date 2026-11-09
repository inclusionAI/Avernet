"""Bots group — ``/openapi/v1/bots`` endpoints.

Public handlers that delegate to the existing internal bot services and wrap the
result in the standard :class:`Envelope` / :class:`Page` contracts. Identity is
the end user the request names in ``?user_id=`` (validated to the verified
caller via ``UserIdDep``); the bot owner a read or write addresses comes from
``OwnerIdDep`` — the caller by default, a shared bot's owner when the request
names one. ``check-name`` asks a tenant-wide question and takes neither. The
request tenant is bound by ``AvernetTenantMiddleware``
before the handler runs, so every service read/write is already tenant-scoped by
the Track A guard. Services are obtained with ``Injected`` exactly as the
internal router does.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    EXAMPLE_TRACE_ID,
    USER_SCOPED_403,
    BotIdPath,
    Deleted,
    Envelope,
)
from agentclaw.community.adapters.http.openapi_v1.clusters import (
    ClusterName,
    validate_engine_cluster,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    UnsupportedEngineError,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    OwnerNameDep,
    DelegatedUserDep,
    UserIdDep,
    require_granted_addressed_bot,
    require_granted_own_bot,
)
from agentclaw.community.adapters.http.openapi_v1.creation_grant import (
    grant_the_creating_app,
    withdraw_the_creation_grant,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    created,
    deleted as deleted_envelope,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_space_service import BotSpaceServiceProtocol
from agentclaw.community.api.skill_set_service_factory import (
    SkillSetServiceFactoryProtocol,
)
from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    AuthStatus,
    AuthStatusUnavailableError,
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    BotCreateTemplateValidationMode,
    complete_bot_authorization,
    create_bot_with_authorization,
)
from agentclaw.community.core.bot_management.engines.registry import resolve_status_ext
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.core.bot_copy.repository.protocol import (
    BotCopyRepositoryProtocol,
)
from agentclaw.community.core.bot_copy.status_derive import build_copy_state_summary
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotOperationNotAllowedError,
    generate_bot_id,
    validate_bot_name,
)
from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,
    INTERNAL_ENGINE_TYPES,
    _get_engine_types,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportError, PassportPlugin

from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)

from .schemas import (
    Bot,
    BotAuthPending,
    BotAuthStatus,
    BotAuthStatusPoll,
    BotCreate,
    BotQuotaExceededData,
    BotStatus,
    BotType,
    BotSpaceAssignment,
    BotSpaceUpdate,
    BotUpdate,
    Passport,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

from .converter_bot import _require_service_capable_engine, _to_bot
from .router_listings import router as listings_router
from .router_listings import list_bots as list_bots, query_bot_metadata as query_bot_metadata
from .router_runtime import router as runtime_router
from .router_runtime import _audit_actor as _audit_actor

logger = get_logger()


#: The bot authorization for an application caller, on the own-bot operations
#: of this group.
#:
#: Declared per route here, unlike the four groups that are wholly own-bot and get it
#: at ``include_router``. This group is mixed: it also holds the bots listing
#: (Mode B), the ceiling (C), the name check (OPEN) and bot creation
#: (user-delegated), none of which names a bot — and on those the check would
#: refuse an application outright rather than authorize it. ``admission.py``
#: is the authority on which route is which; ``test_admission_inventory.py``
#: fails if a declaration and a mode disagree.
_GRANT_CHECKED_OWN_BOT = [Depends(require_granted_own_bot)]
_GRANT_CHECKED_ADDRESSED_BOT = [Depends(require_granted_addressed_bot)]

BOT_QUOTA_CONFLICT_RESPONSES = {
    409: {
        "model": Envelope[BotQuotaExceededData],
        "description": "The target Space has no capacity for another Bot.",
    }
}

router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"], route_class=PublicAPIRoute)


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
    bot_type: str,
) -> None:
    """Push renamed identity metadata or fail the completed-update contract."""
    try:
        passport_plugin.update_passport(
            bot_id=bot_id,
            user_id=owner_id,
            bot_name=bot_name,
            bot_desc=bot_desc,
            engine_type=engine_type or DEFAULT_ENGINE_TYPE,
            bot_type=bot_type,
        )
    except PassportError:
        raise
    except Exception as exc:  # noqa: BLE001 — normalize plugin implementations
        raise PassportError(f"Passport metadata update failed: {exc}") from exc


def _require_publicly_creatable_engine(engine: str) -> None:
    """Refuse engines this surface cannot create on or switch to (→ 400).

    Two gates in one: the value must be in the deployment-configured registry
    (``_get_engine_types`` — the same check switch/engine completion uses), and
    it must not be an internal implementation engine (``aicoding`` is the
    internal runtime behind ``claude_code``, not a product engine; its form
    travels on the template snapshot's ``engine_form`` marker instead).
    """
    if engine not in _get_engine_types() or engine in INTERNAL_ENGINE_TYPES:
        raise UnsupportedEngineError(engine)


def _engine_properties_from_body(
    body: BotCreate | BotAuthStatusPoll,
) -> dict[str, Any]:
    """Plain-dict engine_properties for the Core spec.

    The only conversion this adapter performs on engine-owned creation input:
    Pydantic models stay in the HTTP layer, and the bag's contents remain
    opaque here — the engine-selected Core strategy interprets them.
    """
    if body.engine_properties is None:
        return {}
    return body.engine_properties.model_dump(exclude_none=True)


@router.post(
    "",
    status_code=201,
    response_model=Envelope[Bot],
    # USER_DELEGATED: no bot exists yet for a grant to cover, so an application
    # is admitted on the user-level delegation instead — declared through the
    # ``caller`` parameter below — and granted the bot it creates at the start
    # of the creation. See the mode's entry in `admission.py`.
    responses={
        **USER_SCOPED_403,
        **BOT_QUOTA_CONFLICT_RESPONSES,
        202: {
            "model": Envelope[BotAuthPending],
            "description": "Needs user authorization",
            "content": {
                "application/json": {
                    "example": {
                        "code": 202000,
                        "message": "Accepted",
                        "data": {
                            "bot_id": "20260813_a7k2m9p1",
                            "iframe_url": (
                                "https://auth.example.com/passport/consent?flow=f-123"
                            ),
                            "redirect_url": "",
                        },
                        "request_id": EXAMPLE_TRACE_ID,
                    }
                }
            },
        },
    },
)
@envelope_errors
async def create_bot(
    body: BotCreate,
    request: Request,
    owner_id: UserIdDep,
    owner_name: OwnerNameDep,
    caller: DelegatedUserDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
    skill_set_factory: SkillSetServiceFactoryProtocol = Injected(
        SkillSetServiceFactoryProtocol
    ),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
):
    """Create a bot (201), or 202 with authorization URLs when consent is needed.

    On a 202, have the user complete authorization at one of the returned
    URLs, then poll the auth-status endpoint — the bot is only created there,
    on ISSUED. Engine-specific creation properties belong under
    "engine_properties" and are interpreted by the selected engine;
    engine configuration is managed through the engine-config endpoints after
    creation.

    A coding template with bot_type "service" (开启服务) is fulfilled
    directly: the strategy gate admits service for factory-snapshot template
    creates, so the bot is provisioned in its service shape (one BaaS call —
    service-shaped container, BCN registration, draft publish record) with no
    follow-up upgrade. Hand-written application-coding configs stay
    personal-only and answer 409 for bot_type="service".

    An application calling with its own credential and no end user is
    admitted only if the named user has authorized it at the account level
    (`POST /openapi/v1/bots/authorized-apps`), and is then granted the bot it
    creates: the same authorization a user grants by hand on
    `POST /openapi/v1/bots/{bot_id}/authorized-apps`, visible on the bot and
    withdrawable there. The 202 poll finds that grant already in place.
    """
    # Engine-owned creation input is passed through opaquely below; the
    # engine-selected Core strategy owns its semantics and validation, so this
    # adapter stays free of engine-specific business knowledge.
    # Validate the engine against the configured registry FIRST: the cluster rule
    # below treats every non-teclaw value as ACRA, so an unknown engine would
    # otherwise sail through, allocate an id, apply for a Passport, and only fail
    # later at device provisioning — with those side effects already committed.
    _require_publicly_creatable_engine(body.engine)
    # The engine/cluster pair must obey the bijection (ANDC⟺teclaw, ACRA⟺else).
    validate_engine_cluster(body.engine, body.cluster_name)
    _require_service_capable_engine(body.bot_type, body.engine)
    current_space = space_context.resolve_current(
        owner_id=owner_id,
        header_space_id=body.space_id,
    )
    bot_id = generate_bot_id(owner_id, bot_repo)
    # The creating application is granted the bot *before* the creation runs:
    # on the 202 path nothing is created until the user authorizes, and the
    # poll that completes it is bot-scoped, so the grant has to exist by then.
    # A no-op for a human caller. See ``creation_grant.py``.
    grant_the_creating_app(caller, bot_id=bot_id)
    try:
        outcome = create_bot_with_authorization(
            user_id=owner_id,
            nick_name=owner_name,
            bot_id=bot_id,
            spec=BotCreateSpec(
                entity_id=owner_id,
                engine_type=body.engine,
                bot_type=body.bot_type,
                bot_name=body.bot_name,
                bot_desc=body.bot_desc,
                space_id=current_space.numeric_id,
                template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
                engine_properties=_engine_properties_from_body(body),
            ),
            context=BotCreateContext(
                deployment_mode=BotCreateDeploymentMode.CLOUD,
                space_kind=current_space.kind,
                space_quota=True,
            ),
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            auth_rel_plugin=auth_rel_plugin,
            skill_set_factory=skill_set_factory,
        )
    except Exception:
        # The creation did not start — quota, name, Passport — so the grant
        # names a bot that will never exist. Withdrawn best-effort; the
        # caller's real error is what propagates.
        withdraw_the_creation_grant(caller, bot_id=bot_id)
        raise

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


# PublicAPIRoute validates full paths at registration; these are already prefixed.
router.routes.extend(listings_router.routes)

@router.get(
    "/{bot_id}",
    response_model=Envelope[Bot],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_bot(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    bot_copy_repo: BotCopyRepositoryProtocol = Injected(BotCopyRepositoryProtocol),
    apply_repo: BotConfigManifestApplyRepositoryProtocol = Injected(
        BotConfigManifestApplyRepositoryProtocol
    ),
) -> Envelope[Bot]:
    """Get a bot's details."""
    bot = bot_service.get_bot(bot_id, owner_id)
    # A collaborator reading a shared bot gets the record without the owner's
    # template snapshot — see `_to_bot` for why the passthrough ends here.
    bot_record = _to_bot(bot, include_template=(user_id == owner_id))
    # Derive-on-read copy_state: legacy bots (no ac_bot_copy row) → null, so the
    # additive field does not change their response. entity_id is the dst's apply
    # key; fall back to owner_id for personal bots whose entity_id equals it.
    bot_record.copy_state = build_copy_state_summary(
        bot_copy_repo=bot_copy_repo,
        apply_repo=apply_repo,
        env=get_current_env(),
        entity_id=bot.get("entity_id") or bot.get("owner_id") or owner_id,
        bot_id=bot_id,
        bot_status=bot.get("status"),
    )
    return envelope(bot_record, request)


@router.put(
    "/{bot_id}",
    response_model=Envelope[Bot],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def update_bot(
    bot_id: BotIdPath,
    body: BotUpdate,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
) -> Envelope[Bot]:
    """Update a bot's name/description (engine is fixed at creation).

    The cluster is engine-derived and the engine is immutable, so neither is
    updatable here; engine options are managed via the engine-config
    endpoints. Sending either fails validation with the field named.
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
            bot_type=str(bot.get("bot_type") or "personal"),
        )
    return envelope(_to_bot(bot), request)


@router.put(
    "/{bot_id}/space",
    response_model=Envelope[BotSpaceAssignment],
    responses={**USER_SCOPED_403, **BOT_QUOTA_CONFLICT_RESPONSES},
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def change_bot_space(
    bot_id: BotIdPath,
    body: BotSpaceUpdate,
    request: Request,
    user_id: UserIdDep,
    service: BotSpaceServiceProtocol = Injected(BotSpaceServiceProtocol),
) -> Envelope[BotSpaceAssignment]:
    """Change the Space that owns a Bot.

    The Bot must be owned by `user_id` and that user must currently be a
    member of the target Space. Applications may call this only for an owned
    Bot explicitly delegated to them. A personal Space is selected by its
    numeric id from the Spaces API; `null` is not an implicit shortcut.
    """
    result = service.change_space(
        bot_id=bot_id, owner_id=user_id, space_id=body.space_id
    )
    return envelope(
        BotSpaceAssignment(
            bot_id=result.bot["bot_id"],
            space_id=result.space.id,
            space_code=result.space.space_code,
            space_name=result.space.name,
            space_type=result.space.space_type.value,
            changed=result.changed,
        ),
        request,
    )


@router.delete(
    "/{bot_id}",
    response_model=Envelope[Deleted],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def delete_bot(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Deleted]:
    """Delete a bot.

    Refused (409) for bots whose lifecycle lives elsewhere: desktop bots are
    managed by the desktop service, and service bots are deleted through
    their publish lifecycle.
    """
    _reject_unowned_lifecycle(bot_service.get_bot(bot_id, owner_id), deleting=True)
    bot_service.delete_bot(bot_id, owner_id)
    return deleted_envelope(request)


@router.post(
    "/{bot_id}/restart",
    response_model=Envelope[Bot],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def restart_bot(
    bot_id: BotIdPath,
    request: Request,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Bot]:
    """Restart a bot's container.

    Also what applies a changed startup script. Refused (409) for desktop
    bots, whose lifecycle is managed by the desktop service, and for bots in
    a lifecycle state a restart cannot leave.
    """
    _reject_unowned_lifecycle(bot_service.get_bot(bot_id, owner_id))

    bot = await bot_service.restart_bot_async(bot_id=bot_id, user_id=owner_id)
    # The result carries the base record, so a collaborator restarting a
    # shared bot gets it without the owner's template snapshot (`_to_bot`).
    return envelope(
        _to_bot(bot, include_template=(user_id == owner_id)), request
    )


#: Response table shared by the auth-status poll and its retiring GET spelling
#: (``deprecated/auth_status.py``) — one declaration, so the two cannot drift.
#:
#: The 400 is the one documented exception to the surface-wide ErrorEnvelope
#: (whose ``data`` is null): a terminal authorization state is reported as a
#: failure, but the state itself is the actionable part, so it stays in
#: ``data``. Declared here so generated clients deserialize it against the
#: model it actually returns.
AUTH_STATUS_RESPONSES = {
    **USER_SCOPED_403,
    **BOT_QUOTA_CONFLICT_RESPONSES,
    400: {
        "model": Envelope[BotAuthStatus],
        "description": "Authorization did not complete; `data.status` "
        "carries the terminal state (e.g. REJECTED, EXPIRED)",
        "content": {
            "application/json": {
                "example": {
                    "code": 400000,
                    "message": "Authorization did not complete",
                    "data": {
                        "status": "REJECTED",
                        "message": None,
                        "bot": None,
                    },
                    "request_id": EXAMPLE_TRACE_ID,
                }
            }
        },
    },
}


def _complete_auth_status(
    *,
    bot_id: str,
    request: Request,
    owner_id: str,
    owner_name: str,
    engine: str | None,
    cluster_name: ClusterName | None,
    bot_name: str | None,
    bot_desc: str | None,
    bot_type: BotType | None,
    space_id: str | None,
    engine_properties: dict[str, Any] | None = None,
    bot_service: BotServiceProtocol,
    passport_plugin: PassportPlugin,
    auth_rel_plugin: AuthRelationshipPlugin,
    space_context: BusinessSpaceContextProtocol,
) -> Envelope[BotAuthStatus] | JSONResponse:
    """Validate the echoed attributes, poll Passport, and map the outcome.

    The one implementation behind both spellings of the poll — the POST (body)
    and the retiring GET (query parameters). The two must answer identically,
    and sharing the body is what makes that a property rather than a promise.
    That includes the passport-not-ready answer below: a wait must not read as
    an outage on either spelling.
    """
    # Validate against the engine completion will actually use, not against the
    # supplied value: omitting ``engine`` does not mean "no engine", it means
    # the default one. Checking only when ``engine`` was supplied let
    # ``cluster_name=ANDC`` alone through, and the bot was then provisioned on
    # the ACRA default — a success response contradicting the request.
    effective_engine = engine if engine is not None else DEFAULT_ENGINE_TYPE
    # Check the engine completion will actually use, supplied or defaulted. A
    # deployment whose ENGINE_TYPES excludes openclaw would otherwise create a
    # bot on the default engine anyway — and since creation now persists the
    # configured registry, that bot's active_engine would be absent from its own
    # enabled-engine list. Runs before Passport is queried, so nothing external
    # happens for a request that cannot succeed.
    _require_publicly_creatable_engine(effective_engine)
    if cluster_name is not None:
        validate_engine_cluster(effective_engine, cluster_name)
    _require_service_capable_engine(bot_type or "personal", effective_engine)
    current_space = space_context.resolve_current(
        owner_id=owner_id,
        header_space_id=space_id,
    )
    try:
        result = complete_bot_authorization(
            user_id=owner_id,
            nick_name=owner_name,
            bot_id=bot_id,
            spec=BotCreateSpec(
                entity_id=owner_id,
                engine_type=engine or DEFAULT_ENGINE_TYPE,
                bot_type=bot_type or "personal",
                bot_name=bot_name,
                bot_desc=bot_desc,
                space_id=current_space.numeric_id,
                template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
                engine_properties=engine_properties or {},
            ),
            context=BotCreateContext(
                deployment_mode=BotCreateDeploymentMode.CLOUD,
                space_kind=current_space.kind,
                space_quota=True,
            ),
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            auth_rel_plugin=auth_rel_plugin,
        )
    except AuthStatusUnavailableError:
        # The passport service answered with no status at all — typically the
        # apply is still propagating and the Passport is not ready yet. On this
        # public surface that is a wait, not a fault: the 502 it used to map to
        # made every caller's first poll after a 202 look like an outage.
        # PENDING keeps the documented poll loop intact — PENDING/ISSUED are
        # the two non-terminal states — and the message says what the wait is.
        # The internal /api/bots/auth-status route keeps raising; only this
        # surface's two spellings answer the wait as a wait.
        return envelope(
            BotAuthStatus(
                status=AuthStatus.PENDING,
                message="Passport is not ready yet; keep polling.",
                bot=None,
            ),
            request,
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


@router.post(
    "/{bot_id}/auth-status",
    response_model=Envelope[BotAuthStatus],
    responses=AUTH_STATUS_RESPONSES,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def poll_bot_auth_status(
    bot_id: BotIdPath,
    body: BotAuthStatusPoll,
    request: Request,
    owner_id: UserIdDep,
    owner_name: OwnerNameDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
) -> Envelope[BotAuthStatus]:
    """Poll authorization for a pending creation; the bot is created on ISSUED.

    A POST because this operation is not a read: on the 202 create flow the
    bot is only actually created here, once authorization is granted. The
    caller must re-supply the attributes it created with — the body fields
    mirror the create body and are forwarded to completion. Omit them and the
    bot is created with defaults that contradict what was requested, so
    always echo back engine, cluster_name, bot_name, bot_desc, bot_type and
    space_id when polling.

    Every restriction create enforces is re-applied to the echoed values:
    the same engine registry check, the same engine/cluster pairing, the same
    personal/service restriction on bot_type, and the same business-space
    resolution. An echoed bot_type "service" coding create completes the
    same direct service-create the create endpoint offers.

    While the authorization service has no status for the bot yet — the
    Passport is not ready — the poll answers PENDING with a message saying
    so, rather than an error: keep polling.
    """
    return _complete_auth_status(
        bot_id=bot_id,
        request=request,
        owner_id=owner_id,
        owner_name=owner_name,
        engine=body.engine,
        cluster_name=body.cluster_name,
        bot_name=body.bot_name,
        bot_desc=body.bot_desc,
        bot_type=body.bot_type,
        space_id=body.space_id,
        engine_properties=_engine_properties_from_body(body),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
        space_context=space_context,
    )


@router.get(
    "/{bot_id}/status",
    response_model=Envelope[BotStatus],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_ADDRESSED_BOT,
)
@envelope_errors
async def get_bot_status(
    bot_id: BotIdPath,
    request: Request,
    owner_id: OwnerIdDep,
    user_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[BotStatus]:
    """Get a bot's runtime / device readiness."""
    bot = bot_service.get_bot(bot_id, owner_id)
    binding = bot.get("device_binding") or {}
    return envelope(
        BotStatus(
            ext=resolve_status_ext(bot, response_ext={}),
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
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def get_bot_passport(
    bot_id: BotIdPath,
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
    # License fields are forwarded exactly as the legacy ``/api`` passport
    # endpoint forwarded the plugin dict verbatim — both implementations
    # currently return ``None`` for them, so they carry the same "unknown until
    # the data source backfills" value here. ``passport_id`` is still the key
    # existence signal; license fields are presentation only.
    return envelope(
        Passport(
            bot_id=bot_id,
            passport_id=passport_id,
            expire_at=info.get("expire_at"),
            certificate_url=info.get("certificate_url"),
        ),
        request,
    )


router.routes.extend(runtime_router.routes)
