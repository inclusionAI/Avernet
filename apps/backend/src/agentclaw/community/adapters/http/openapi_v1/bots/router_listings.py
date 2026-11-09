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

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    Envelope,
    NameCheck,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.log_safe import for_log
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep,
    UserIdDep,
    require_granted_addressed_bot,
    require_granted_own_bot,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from .dependencies_stage_listing import list_stage_bots
from agentclaw.community.core.bot_inventory.protocols import BotInventoryTemplatePort
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.bot_quota_service import BotQuotaServiceProtocol
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    validate_bot_name,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from agentclaw.community.api.bot_inventory_service import (
    BotInventoryServiceProtocol,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_inventory.types import (
    DeployMode as CoreDeployMode,
)

from .converter_inventory import to_inventory_item as _to_inventory_item
from .schemas import (
    Bot,
    BotMetadata,
    BotMetadataQueries,
    BotInventoryItem,
    BotQuotaExceededData,
    Ceiling,
    DeployMode,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

from agentclaw.community.core.bot_copy.status_derive import build_copy_state_summary
from agentclaw.community.core.bot_copy.repository.protocol import (
    BotCopyRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot.config_manifest_apply import (
    BotConfigManifestApplyRepositoryProtocol,
)
from agentclaw.community.utils.env_utils import get_current_env

from .converter_bot import _to_bot, _resolve_row_spaces, _to_bot_metadata

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


@router.get("", response_model=Envelope[Page[Bot]], responses=USER_SCOPED_403)
@envelope_errors
async def list_bots(
    request: Request,
    page_params: PageParamsDep,
    owner_id: UserIdDep,
    caller: ActingCallerDep,
    stage: Annotated[RuntimeStage, Query(description="Filter service Bots by runtime stage before pagination; defaults to draft. Other Bot kinds are unchanged.")] = RuntimeStage.DRAFT,
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
    bot_repo: BotRepository = Injected(BotRepository),
    template_port: BotInventoryTemplatePort = Injected(BotInventoryTemplatePort),
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
    filters = dict(
        owner_id=owner_id,
        bot_name=keyword,
        engine=engine,
        status=status,
        page=page_params.page,
        page_size=page_params.page_size,
        bot_ids=sorted(granted) if granted is not None else None,
    )
    result = await asyncio.to_thread(
        list_stage_bots, bot_repo, template_port, stage=stage.value, **filters,
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
    result = await asyncio.to_thread(
        bot_service.list_bots_by_owner_bot_pairs,
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
    # holding some live delegation from that user — a user-level one, or any
    # bot grant: proof of a relationship, the closest thing this operation has
    # to a scope. An application with no delegation learns nothing it did not
    # already know.
    #
    if not caller.holds_delegation():
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
    bot_copy_repo: BotCopyRepositoryProtocol = Injected(BotCopyRepositoryProtocol),
    apply_repo: BotConfigManifestApplyRepositoryProtocol = Injected(
        BotConfigManifestApplyRepositoryProtocol
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
    # Derive-on-read copy_state per inventory card (same source as GET /bots/{id}):
    # legacy bots (no ac_bot_copy row) → null, additive field. Mirrors the detail
    # handler's attach so inventory cards render the copy-progress badge + retry
    # action without a second round-trip.
    inventory_items = []
    for item in items:
        inventory_item = _to_inventory_item(item)
        inventory_item.copy_state = build_copy_state_summary(
            bot_copy_repo=bot_copy_repo,
            apply_repo=apply_repo,
            env=get_current_env(),
            entity_id=item.owner_entity_id or owner_id,
            bot_id=item.bot_id,
            bot_status=item.status,
        )
        inventory_items.append(inventory_item)
    return page(total, inventory_items, request)
