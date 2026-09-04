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

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    EXAMPLE_TRACE_ID,
    STARTUP_SCRIPT_WRITE_RESPONSES,
    USER_SCOPED_403,
    BotIdPath,
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
from agentclaw.community.adapters.http.openapi_v1.errors import (
    StartupScriptUnsupportedError,
    UnsupportedEngineError,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
    refuse_app_only_caller,
    require_granted_own_bot,
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
from agentclaw.community.api.bot_quota_service import BotQuotaServiceProtocol
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
    ServiceIntakeSeam,
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
from agentclaw.community.api.bot_startup_script_service import (
    SUPPORTED,
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.api.service_publication_facade import (
    ServicePublicationFacadeProtocol,
)
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationConflictError,
)
from agentclaw.community.api.data_init_service import DataInitServiceProtocol
from agentclaw.community.core.workspace.constants import (
    DEFAULT_ENGINE_TYPE,
    INTERNAL_ENGINE_TYPES,
    _get_engine_types,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
from agentclaw.community.plugin_api.passport import PassportError, PassportPlugin

from agentclaw.community.api.bot_inventory_service import (
    BotInventoryServiceProtocol,
)
from agentclaw.community.api.bot_dormant_service import (
    BotDormantActivateServiceProtocol,
)
from agentclaw.community.core.bot_inventory.errors import (
    BotInventoryPermissionError,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_inventory.policies.combo_policy import (
    assert_service_upgrade,
)
from agentclaw.community.core.bot_management.template_public_view import (
    template_config_for_public,
)
from agentclaw.community.core.bot_inventory.types import (
    BotInventoryItem as CoreItem,
    DeployMode as CoreDeployMode,
)
from agentclaw.community.core.service_bot.errors import (
    ServicePublicationUnsupportedError,
)

from agentclaw.community.core.services.engine_config import (
    engine_config_coords_from_bot,
)
from .startup_script_support import (
    _startup_script_payload,
    _startup_script_target,
    _withdraw_the_write_if_the_bot_was_deleted,
)
from .schemas import (
    Bot,
    BotMetadata,
    BotMetadataQueries,
    BotActivateResult,
    BotAuthPending,
    BotAuthStatus,
    BotAuthStatusPoll,
    BotCreate,
    BotInventoryItem,
    BotQuotaExceededData,
    BotStatus,
    BotType,
    BotSpaceAssignment,
    BotSpaceUpdate,
    BotUpdate,
    Ceiling,
    DataInitRequest,
    DataInitResult,
    DeployMode,
    Passport,
    StartupScript,
    StartupScriptWrite,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

logger = get_logger()


#: The bot authorization for an application caller, on the own-bot operations
#: of this group.
#:
#: Declared per route here, unlike the four groups that are wholly own-bot and get it
#: at ``include_router``. This group is mixed: it also holds the bots listing
#: (Mode B), the ceiling (C), the name check (OPEN) and bot creation (refused),
#: none of which names a bot — and on those the check would refuse an
#: application outright rather than authorize it. ``admission.py`` is the
#: authority on which route is which; ``test_admission_inventory.py`` fails if
#: a declaration and a mode disagree.
_GRANT_CHECKED_OWN_BOT = [Depends(require_granted_own_bot)]

#: What a ``REFUSED`` operation declares: no caller without an end user. The
#: refusal already happens centrally in ``require_principal`` — this makes the
#: decision visible on the route that carries it, and holds even if the table
#: entry were ever mislabelled. See ``refuse_app_only_caller``.
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]

BOT_QUOTA_CONFLICT_RESPONSES = {
    409: {
        "model": Envelope[BotQuotaExceededData],
        "description": "The target Space has no capacity for another Bot.",
    }
}

router = APIRouter(prefix="/openapi/v1/bots", tags=["bots"], route_class=PublicAPIRoute)


def _require_service_capable_engine(bot_type: str, engine: str) -> None:
    if bot_type != "service":
        return
    decision = assert_service_upgrade(engine)
    if not decision.ok:
        raise ServicePublicationUnsupportedError(
            decision.reason or "engine cannot be used by a service bot"
        )


class _FacadeServiceIntakeSeam:
    """Adapt the publication facade to the create flow's service intake seam.

    The completion poll is the retry surface for a create-as-service request,
    so a replayed conversion hits the facade's already-service conflict — for
    intake that is success (the goal state is reached), not a failure to
    surface to the caller.
    """

    def __init__(self, facade: ServicePublicationFacadeProtocol) -> None:
        self._facade = facade

    def convert(self, bot_id: str, *, actor_id: str, owner_id: str) -> None:
        try:
            self._facade.convert_to_service(
                bot_id, actor_id=actor_id, owner_id=owner_id
            )
        except ServicePublicationConflictError as exc:
            logger.info(
                "[service_intake] conversion replay for already-serviced bot: "
                "bot_id=%s, reason=%s",
                bot_id,
                exc,
            )


def _to_bot(d: dict[str, Any], *, space: dict[str, Any] | None = None) -> Bot:
    """Adapt an internal bot ``to_dict()`` record to the public ``Bot`` schema.

    ``template_config`` on the row is the stored engine snapshot, returned
    verbatim (2026-09-01 passthrough decision — an owner-scoped face echoes
    the caller's own creation input, secrets included), detached via the core
    deep-copy helper — gated on a truthy ``template_type`` so the detail path
    (whose attach is not template-gated) honors the same "null without a
    template" contract the listings publish. ``space`` is the owner-view
    summary the listing endpoints resolve and pass in; other callers leave it
    null.
    """
    engine = d.get("active_engine") or ""
    has_template = d.get("template_type") not in (None, "")
    return Bot(
        bot_id=d["bot_id"],
        bot_name=d.get("bot_name") or "",
        bot_desc=d.get("bot_desc") or "",
        engine=engine,
        cluster_name=cluster_for_engine(engine),
        bot_type=d.get("bot_type") or "",
        status=d.get("status") or "",
        owner_entity_id=d.get("owner_id") or "",
        template_type=str(d["template_type"]) if has_template else None,
        template_config=(
            template_config_for_public(d.get("template_config"))
            if has_template
            else None
        ),
        space=space,
    )


def _to_public_space(ref: Any) -> dict[str, Any] | None:
    """Flatten a ``BusinessSpaceRef`` for pydantic coercion into ``BusinessSpace``."""
    if ref is None:
        return None
    return {"space_id": ref.space_id, "name": ref.name, "kind": ref.kind}


def _resolve_row_spaces(
    space_context: BusinessSpaceContextProtocol,
    rows: list[dict[str, Any]],
    *,
    owner_id: str,
) -> dict[str, dict[str, Any] | None]:
    """Owner-view space summary per distinct ``ac_bots.space_id``.

    Memoized on the raw space_id column: one ``bot_space`` resolution per
    distinct space per page. This is plain memo caching — the semantics
    (synthetic ``personal:<user>`` fallback, member-gated None) stay in the
    core ``BusinessSpaceContextProtocol``. A space the resolver refuses
    (missing membership row on a legacy record and the like) degrades to
    ``space: null`` for that row, mirroring how ``_list_cloud_rows`` skips a
    row the space module rejects: one bad record must not take the whole
    listing page down.
    """
    resolved: dict[str, dict[str, Any] | None] = {}
    for row in rows:
        raw = str(row.get("space_id") or "")
        if raw in resolved:
            continue
        try:
            ref = space_context.bot_space(
                bot=row, owner_id=owner_id, current_space=None
            )
        except BotInventoryPermissionError:
            ref = None
        resolved[raw] = _to_public_space(ref)
    return resolved


def _to_bot_metadata(d: dict[str, Any]) -> BotMetadata:
    """Project a Bot record onto the deliberately display-only batch contract."""
    return BotMetadata(
        bot_id=d["bot_id"],
        owner_id=d.get("owner_id") or "",
        bot_name=d.get("bot_name") or "",
        bot_desc=d.get("bot_desc") or "",
        engine=d.get("active_engine") or "",
        bot_type=d.get("bot_type") or "",
        status=d.get("status") or "",
    )


def _to_inventory_item(item: CoreItem) -> BotInventoryItem:
    """Adapt a core ``BotInventoryItem`` value object to the public schema.

    ``space`` is forwarded as a plain dict — pydantic coerces it into a
    ``BusinessSpace``. ``actions`` is a tuple of enums on the core side and a
    list of strings on the public side; ``disabled_actions`` mirrors that.
    """
    space = None
    if item.space is not None:
        space = {
            "space_id": item.space.space_id,
            "name": item.space.name,
            "kind": item.space.kind,
        }
    edit_lock = None
    if item.edit_lock is not None:
        edit_lock = {
            "locked": item.edit_lock.locked,
            "acquired": None,
            "holder_user_id": item.edit_lock.holder_user_id,
            "holder_name": item.edit_lock.holder_name,
            "has_collaborators": item.edit_lock.has_collaborators,
            "is_owner_holder": item.edit_lock.is_owner_holder,
            "need_lock": item.edit_lock.need_lock,
        }
    return BotInventoryItem(
        bot_id=item.bot_id,
        card_id=item.card_id,
        bot_name=item.bot_name,
        bot_desc=item.bot_desc,
        engine=item.engine,
        bot_type=item.bot_type,
        kind=item.kind.value,
        deploy_mode=item.deploy_mode.value,
        display_state=item.display_state.value,
        status=item.status,
        publication_id=item.publication_id,
        publication_version=item.publication_version,
        live_version=item.live_version,
        internal_status=item.internal_status,
        owner_entity_id=item.owner_entity_id,
        space=space,
        template_type=item.template_type,
        template_config=dict(item.template_config) if item.template_config else None,
        avatar_url=item.avatar_url,
        machine_id=item.machine_id,
        mount_path=item.mount_path,
        passport_id=item.passport_id,
        actions=[a.value for a in item.actions],
        disabled_actions=dict(item.disabled_actions) if item.disabled_actions else None,
        edit_lock=edit_lock,
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
    """Push renamed identity metadata or fail the completed-update contract."""
    try:
        passport_plugin.update_passport(
            bot_id=bot_id,
            user_id=owner_id,
            bot_name=bot_name,
            bot_desc=bot_desc,
            engine_type=engine_type or DEFAULT_ENGINE_TYPE,
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
    # REFUSED to a machine caller: no bot exists yet for a grant to cover, and
    # creation spends the user's quota — see the mode's entry in `admission.py`.
    dependencies=_REFUSES_APP_ONLY,
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
    service_publication_facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
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
    orchestratively: the bot is created by the personal-only template path and
    immediately upgraded to a service bot, which is what the 201 reports. If
    the upgrade fails the response is a 502 naming the created-as-personal
    bot — retry via the lifecycle upgrade instead of re-creating.
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
            space_id=current_space.numeric_id,
            template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
            engine_properties=_engine_properties_from_body(body),
        ),
        context=BotCreateContext(
            deployment_mode=BotCreateDeploymentMode.CLOUD,
            space_kind=current_space.kind,
            space_quota=True,
            service_intake=True,
        ),
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        auth_rel_plugin=auth_rel_plugin,
        skill_set_factory=skill_set_factory,
        service_intake_seam=_FacadeServiceIntakeSeam(service_publication_facade),
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
    keyword: Annotated[
        str | None,
        Query(description="Filter: bots whose name contains this text."),
    ] = None,
    engine: Annotated[
        str | None,
        Query(description="Filter: only bots on this engine, matched exactly."),
    ] = None,
    status: Annotated[
        str | None,
        Query(
            description="Filter: only bots in this lifecycle status, matched "
            "exactly (e.g. 'ACTIVE'; see the Bot schema for the vocabulary)."
        ),
    ] = None,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
) -> Envelope[Page[Bot]]:
    """List the caller's bots, narrowed to the caller's authorized scope.

    Human callers see their own bots. Application callers see only owned bots
    explicitly granted by the delegating user. The grant restriction is passed
    to the service before pagination.

    An application granted nothing gets an empty page, not an error: naming no
    bot, this operation has nothing to mask. The complete view of delegated
    bots, including bots the user does not own, is the authorized-bots listing.

    The keyword, engine, and status filters are applied before pagination.
    Each row carries the owner-view space of its space assignment and the
    projected template snapshot. For richer inventory fields such as
    deployment mode, use GET /openapi/v1/bots/all.
    """
    granted = caller.granted_bot_ids(owned_by_delegator=True)
    if granted is not None and not granted:
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
    rows = result["items"]
    # Off the event loop: the resolution issues synchronous space-service
    # reads, one per distinct space on the page — a page over many spaces
    # must not stall every other request the handler is interleaved with.
    row_spaces = await asyncio.to_thread(
        _resolve_row_spaces, space_context, rows, owner_id=owner_id
    )
    items = [
        _to_bot(b, space=row_spaces.get(str(b.get("space_id") or "")))
        for b in rows
    ]
    return page(result["total"], items, request)


@router.post(
    "/metadata/queries",
    response_model=Envelope[Page[BotMetadata]],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def query_bot_metadata(
    body: BotMetadataQueries,
    page_params: PageParamsDep,
    request: Request,
    user_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Page[BotMetadata]]:
    """Resolve display metadata for caller-supplied Bot and owner pairs.

    The identifiers may originate from BCN, search, recommendations, persisted
    client state, or another source. The user_id parameter names the authenticated
    user performing this tenant-wide metadata lookup; each owner_id in the body
    is part of a target Bot identity, not the caller identity. The response is
    intentionally limited to display fields and the owner id the request already
    named; it never exposes device bindings, runtime configuration, credentials,
    or extension payloads.

    Unknown identifiers are omitted. Filtering happens in the repository before
    pagination, so total is the number of matching Bot records.
    """
    del user_id  # UserIdDep has already enforced equality with the Principal.
    pairs = list(dict.fromkeys((item.bot_id, item.owner_id) for item in body.bots))
    result = bot_service.list_bots_by_owner_bot_pairs(
        page=page_params.page,
        page_size=page_params.page_size,
        pairs=pairs,
    )
    items = [_to_bot_metadata(item) for item in result["items"]]
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
    name: Annotated[
        str,
        Query(
            description="The bot name to check. Validated with the same rules "
            "create applies (non-blank, no '@'), so an invalid name is a 400 "
            "here rather than a false 'available'."
        ),
    ],
    request: Request,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[NameCheck]:
    """Check whether a bot name is available (within the caller's tenant).

    Applies the same validation create and update do, so "available" always
    means "you could create this". The echoed name is the trimmed form
    actually checked — a caller that sends " Foo " sees which string the
    availability answer applies to.
    """
    # The same rule as create/update on purpose: the repository lookup alone
    # answers False for a blank or @-bearing name, which would report a name
    # as free that the very next request rejects (400). Rejecting here keeps
    # one answer across the three endpoints.
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
    x_space_id: Annotated[
        str | None,
        Header(
            alias="X-Space-Id",
            description="Business-space context; omit to use the personal space.",
        ),
    ] = None,
    quota_service: BotQuotaServiceProtocol = Injected(BotQuotaServiceProtocol),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
) -> Envelope[Ceiling]:
    """Get the Bot ceiling for the selected business Space.

    Personal Space keeps the named user's configured ceiling. Team Space uses
    its own ceiling and counts Bots created by every member. For an application
    caller, reading it still requires at least one live delegation from the
    named user; without one the user is answered as if they did not exist.
    """
    # Names no bot, so there is no grant to check against one — but the answer
    # is still about a person's account, and a stranger application must not be
    # able to read it by naming a user id. So it is gated on the application
    # holding at least one live delegation from that user: proof of a
    # relationship, the closest thing this operation has to a scope. An
    # application with no delegation learns nothing it did not already know.
    #
    granted = caller.granted_bot_ids()
    if granted is not None and not granted:
        # The named user goes to the log bounded and escaped, never into the
        # exception message: that message reaches a log line verbatim, and
        # ``user_id`` is declared ``min_length=1`` with no upper bound, so raw
        # it would let a refused caller forge log lines and choose how many
        # bytes each refusal costs.
        logger.warning(
            "[bots] app holds no delegation from user=%s; refusing the ceiling",
            for_log(owner_id),
        )
        raise BotNotFoundError("no authorization from the named user")
    current_space = space_context.resolve_current(
        owner_id=owner_id, header_space_id=x_space_id
    )
    snapshot = quota_service.inspect(
        owner_id=owner_id, space_id=current_space.numeric_id
    )
    return envelope(Ceiling(ceiling=snapshot.ceiling), request)


# ── Bot inventory card surface ─────────────────────────────────────────────
# Card list at ``/openapi/v1/bots/all``, declared before the ``/{bot_id}``
# wildcard so ``all`` matches as a literal rather than a bot_id. Each card already
# carries its action affordances; the former rich-card detail and standalone
# actions endpoints were removed. The list aggregates the owner's personal cloud,
# service, and local Bots behind ``BotInventoryServiceProtocol`` (a distinct Service API
# from the ``BotServiceProtocol`` CRUD below); ``_to_inventory_item`` translates
# the read model to the public schema.


@router.get(
    "/all",
    response_model=Envelope[Page[BotInventoryItem]],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def list_inventory(
    page_params: PageParamsDep,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    request: Request,
    x_space_id: Annotated[
        str | None,
        Header(
            alias="X-Space-Id",
            description="Business-space context for the inventory; omit to use the personal space.",
        ),
    ] = None,
    keyword: Annotated[
        str | None,
        Query(description="Filter inventory items whose bot name contains this text."),
    ] = None,
    engine: Annotated[
        str | None,
        Query(description="Filter inventory items by engine, matched exactly."),
    ] = None,
    deploy_mode: Annotated[
        DeployMode | None,
        Query(description="Filter inventory items by cloud or local deployment."),
    ] = None,
    is_service: Annotated[
        bool | None,
        Query(
            description=(
                "Filter by service classification: true returns service Bots, "
                "false returns non-service Bots, and omission returns both."
            )
        ),
    ] = None,
    service: BotInventoryServiceProtocol = Injected(BotInventoryServiceProtocol),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
) -> Envelope[Page[BotInventoryItem]]:
    """List personal cloud, service, and local Bots in the current space."""
    granted = caller.granted_bot_ids(owned_by_delegator=True)
    if granted is not None and not granted:
        return page(0, [], request)
    current_space = space_context.resolve_current(
        owner_id=owner_id,
        header_space_id=x_space_id,
    )
    items, total = service.list_items(
        owner_id=owner_id,
        space=current_space,
        keyword=keyword,
        engine=engine,
        deploy_mode=CoreDeployMode(deploy_mode) if deploy_mode is not None else None,
        is_service=is_service,
        bot_ids=sorted(granted) if granted is not None else None,
        page=page_params.page,
        page_size=page_params.page_size,
    )
    return page(total, [_to_inventory_item(item) for item in items], request)


# ── Dormant Bot activation ─────────────────────────────────────────────────
# ``POST /openapi/v1/bots/{bot_id}/activate`` — a two-segment sub-resource of
# the bot record (like ``/{bot_id}/restart``), so it follows ``/{bot_id}`` and
# needs no literal guard. The handler does the owner lookup + bot_type guard
# itself and delegates only the reactivation orchestration to
# ``BotDormantActivateServiceProtocol`` (``ActivateBotService.activate``);
# local bots are never reclaimed by dormant so they are refused here (409),
# service bots go through their own publish flow.


def _require_personal_cloud_bot(bot: dict[str, Any]) -> None:
    """Refuse dormant activation for non-personal-cloud bots (→ 409).

    ``bot_type`` is the only field that distinguishes a personal cloud bot from
    a desktop or service bot at this layer; ``status`` is checked downstream
    by ``ActivateBotService.activate`` (RECYCLED only).
    """
    bot_type = bot.get("bot_type") or ""
    if bot_type == "desktop":
        raise BotOperationNotAllowedError(
            "local bots are not reclaimed by dormant activation"
        )
    if bot_type == "service":
        raise BotOperationNotAllowedError(
            "service bot lifecycle is owned by the publish flow"
        )
    if bot_type != "personal":
        raise BotOperationNotAllowedError(
            f"dormant activation is not supported for bot_type: {bot_type or 'unknown'}"
        )


@router.post(
    "/{bot_id}/activate",
    response_model=Envelope[BotActivateResult],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def activate_dormant_bot(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    activate_service: BotDormantActivateServiceProtocol = Injected(
        BotDormantActivateServiceProtocol
    ),
) -> Envelope[BotActivateResult]:
    """Activate a recycled personal cloud bot.

    Returns 404 when the bot is not visible to the caller and 409 when the bot
    is not a recycled personal cloud bot.
    """
    bot = bot_service.get_bot(bot_id, owner_id)
    _require_personal_cloud_bot(bot)
    result = activate_service.activate(bot_id=bot_id, user_id=owner_id)
    return envelope(
        BotActivateResult(
            bot_id=bot_id,
            status=str(result.get("status") or ""),
            message=result.get("message"),
        ),
        request,
    )


@router.get(
    "/{bot_id}",
    response_model=Envelope[Bot],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def get_bot(
    bot_id: BotIdPath,
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
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def restart_bot(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
) -> Envelope[Bot]:
    """Restart a bot's container.

    Also what applies a changed startup script. Refused (409) for desktop
    bots, whose lifecycle is managed by the desktop service, and for bots in
    a lifecycle state a restart cannot leave.
    """
    _reject_unowned_lifecycle(bot_service.get_bot(bot_id, owner_id))
    bot = bot_service.restart_bot(bot_id, owner_id)
    return envelope(_to_bot(bot), request)


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
    # The POST spelling passes the orchestrating seam so an echoed
    # bot_type=service coding create completes the upgrade; the retiring GET
    # is plain-bot-only (no engine_properties to echo), so it passes nothing
    # and keeps its historical behavior.
    service_intake_seam: ServiceIntakeSeam | None = None,
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
            nick_name=owner_id,
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
                service_intake=True,
            ),
            bot_service=bot_service,
            passport_plugin=passport_plugin,
            auth_rel_plugin=auth_rel_plugin,
            service_intake_seam=service_intake_seam,
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
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    passport_plugin: PassportPlugin = Injected(PassportPlugin),
    auth_rel_plugin: AuthRelationshipPlugin = Injected(AuthRelationshipPlugin),
    space_context: BusinessSpaceContextProtocol = Injected(
        BusinessSpaceContextProtocol
    ),
    service_publication_facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
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
    same orchestrated upgrade the create endpoint offers.

    While the authorization service has no status for the bot yet — the
    Passport is not ready — the poll answers PENDING with a message saying
    so, rather than an error: keep polling.
    """
    return _complete_auth_status(
        bot_id=bot_id,
        request=request,
        owner_id=owner_id,
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
        service_intake_seam=_FacadeServiceIntakeSeam(service_publication_facade),
    )


@router.get(
    "/{bot_id}/status",
    response_model=Envelope[BotStatus],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def get_bot_status(
    bot_id: BotIdPath,
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


def _audit_actor(caller: ActingCaller, owner_id: str) -> str:
    """Who to record as having changed the script.

    For an application caller ``user_id`` is the *delegating* user, not the
    caller — downstream code cannot tell an admitted application from that user,
    which is the seam's whole point. That is right for scoping and wrong for an
    audit field: recording it would have this executable body attributed to a
    person who did not write it. So an application is named as itself, with the
    user it acted for kept alongside.
    """
    if caller.is_application:
        return f"app:{caller.app_id}:on-behalf-of:{owner_id}"
    return owner_id


@router.get(
    "/{bot_id}/startup-script",
    response_model=Envelope[StartupScript],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def get_bot_startup_script(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    startup_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
) -> Envelope[StartupScript]:
    """Read a bot's startup script.

    A bot that has never had one reads as an empty script, not an error. An
    unsupported bot still answers here — with supported false and a reason —
    so a caller can discover why before trying to write.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id, state, reason = _startup_script_target(bot, startup_script_service)
    record = startup_script_service.get(entity_id=entity_id, bot_id=bot_id)
    return envelope(_startup_script_payload(bot_id, record, state, reason), request)


@router.put(
    "/{bot_id}/startup-script",
    response_model=Envelope[StartupScript],
    responses=STARTUP_SCRIPT_WRITE_RESPONSES,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def update_bot_startup_script(
    bot_id: BotIdPath,
    body: StartupScriptWrite,
    request: Request,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    startup_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
) -> Envelope[StartupScript]:
    """Set or replace a bot's startup script.

    Takes effect the next time the platform composes a start command — a
    restart or republish of the bot does that. Lower-level restarts and
    scale-outs reuse the previously composed configuration, so an instance
    started that way can still run the previously stored script until the bot
    is next restarted or republished.

    Refused for a bot whose container cannot run one: storing it would be a
    silent no-op the caller could not distinguish from success.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    # Desktop bots are refused by ``resolve_support`` itself, not by a guard
    # here: gating only the write made GET answer ``supported: true`` for a bot
    # whose next PUT was certain to fail, which is precisely the discovery path
    # GET exists to provide.
    entity_id, state, reason = _startup_script_target(bot, startup_script_service)
    if state != SUPPORTED:
        raise StartupScriptUnsupportedError(reason)
    record = startup_script_service.put(
        entity_id=entity_id,
        bot_id=bot_id,
        script=body.script,
        # From the verified caller, never the body — and naming the application
        # when one is acting, not the user it acted for.
        modifier=_audit_actor(caller, owner_id),
    )
    _withdraw_the_write_if_the_bot_was_deleted(
        bot_id, entity_id, owner_id, bot_service, startup_script_service
    )
    return envelope(_startup_script_payload(bot_id, record, SUPPORTED, ""), request)


@router.delete(
    "/{bot_id}/startup-script",
    response_model=Envelope[Deleted],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def delete_bot_startup_script(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    startup_script_service: BotStartupScriptServiceProtocol = Injected(
        BotStartupScriptServiceProtocol
    ),
) -> Envelope[Deleted]:
    """Clear a bot's startup script. Idempotent.

    Takes effect the next time the platform composes a start command — see the
    PUT for which starts do and do not recompose. Clearing does not reach an
    already-running container, and does not reach a targeted device restart or
    a scale-out replica until the bot is next restarted or republished.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard
    entity_id = bot.get("entity_id")
    if not entity_id:
        raise BotNotFoundError("bot has no associated entity")
    startup_script_service.delete(entity_id=entity_id, bot_id=bot_id)
    return deleted_envelope(request)


def _require_personal_cloud_bot(bot: dict[str, Any]) -> None:
    bot_type = bot.get("bot_type") or ""
    if bot_type == "desktop":
        raise BotOperationNotAllowedError(
            "local bots do not support data initialization"
        )
    if bot_type == "service":
        raise BotOperationNotAllowedError(
            "service bot data lifecycle is owned by the publish flow"
        )
    if bot_type != "personal":
        raise BotOperationNotAllowedError(
            f"data initialization is not supported for bot_type: {bot_type or 'unknown'}"
        )


def _observe_data_init_task(task: asyncio.Task[dict[str, str]]) -> None:
    """Consume a detached task's exception so failures are never unobserved."""
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "data-init background task failed",
            exc_info=(type(error), error, error.__traceback__),
        )


@router.get(
    "/{bot_id}/data-init",
    response_model=Envelope[DataInitResult],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def get_bot_data_init_status(
    bot_id: BotIdPath,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    data_init_service: DataInitServiceProtocol = Injected(DataInitServiceProtocol),
) -> Envelope[DataInitResult]:
    """Read cold-start initialization state without exposing the bot ext bag."""
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard (→ 404)
    _require_personal_cloud_bot(bot)
    result = data_init_service.get_status(bot_id, owner_id)
    return envelope(DataInitResult(**result), request)


@router.post(
    "/{bot_id}/data-init",
    response_model=Envelope[DataInitResult],
    responses=USER_SCOPED_403,
    dependencies=_GRANT_CHECKED_OWN_BOT,
)
@envelope_errors
async def trigger_bot_data_init(
    bot_id: BotIdPath,
    body: DataInitRequest,
    request: Request,
    owner_id: UserIdDep,
    bot_service: BotServiceProtocol = Injected(BotServiceProtocol),
    data_init_service: DataInitServiceProtocol = Injected(DataInitServiceProtocol),
) -> Envelope[DataInitResult]:
    """Trigger cold-start data initialization for a personal cloud bot.

    The operation returns immediately while work continues in the background.
    Read this resource with GET to observe the persisted state. Local and
    service bots are refused with 409.
    """
    bot = bot_service.get_bot(bot_id, owner_id)  # ownership/tenant guard (→ 404)
    _require_personal_cloud_bot(bot)
    _coords = engine_config_coords_from_bot(bot, bot_id=bot_id, owner_id=owner_id)
    entity_id, entity_type = _coords.entity_id, _coords.entity_type

    # Cookie parsing belongs to the HTTP adapter. The transport-agnostic service
    # decides whether and when the temporary credential must be persisted.
    iam_token = request.cookies.get("IAM_TOKEN") or None
    task = asyncio.create_task(
        data_init_service.trigger_init(
            bot_id=bot_id,
            owner_id=owner_id,
            entity_id=entity_id,
            entity_type=entity_type,
            force=body.force,
            iam_token=iam_token,
        )
    )
    task.add_done_callback(_observe_data_init_task)
    return envelope(
        DataInitResult(
            bot_id=bot_id,
            status="in_progress",
            message="data initialization dispatched",
        ),
        request,
    )
