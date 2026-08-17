"""MCP group — ``/openapi/v1/bots/mcp`` public endpoints.

Public handlers for the MCP marketplace (list / detail / permission), MCP
tenants, and the caller's unified per-server config (list / read / write /
delete, pushed to the caller's devices). They delegate to the same MCP services
the internal ``/api/mcp`` router uses, through the shared ``core/mcp`` flow and
presentation helpers, and wrap the result in the standard :class:`Envelope` /
:class:`Page` contracts.

Everything here is **account-level**: keyed by ``(user_id, server_code)``, with
no bot dimension. Which bots may actually *use* a server is the separate
bot-scoped group at ``/openapi/v1/bots/{bot_id}/mcp`` — deleting a config here
never deactivates a server there, and vice versa.

Identity is the end user the request names in ``?user_id=`` (owner-scoping,
via ``UserIdDep``) — on the five config/permission operations. The three
marketplace catalogue reads reply the same for every caller in the tenant and
take no ``user_id``. The request tenant is bound by ``AvernetTenantMiddleware``
before the handler runs, so every config read/write is already tenant-scoped by
the Track A guard. Unlike the internal detail route, no ``IAM_TOKEN`` cookie is
read — a registered-tenant caller presents no browser cookie, and forwarding one
below the adapter boundary is out of scope for this surface (same stance the
bots slice took).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import (
    USER_SCOPED_403,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import (
    UserIdDep,
    refuse_app_only_caller,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page,
)
from agentclaw.community.api.mcp_auth_service import MCPAuthServiceProtocol
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.core.mcp.config_flow import (
    delete_unified_config,
    list_marketplace_servers,
    list_marketplace_tenants,
    list_unified_configs,
    read_unified_config,
    write_unified_config,
)
from agentclaw.community.core.mcp.errors import McpServerNotFoundError
from agentclaw.community.core.mcp.presentation import (
    ALLOWED_NETWORK_TYPES,
    is_network_type_visible,
    normalize_network_types,
    primary_transport_protocol,
    strip_ext_info,
)
from agentclaw.community.di import Injected

from .schemas import (
    McpConfig,
    McpConfigDeleted,
    McpConfigWrite,
    McpPermission,
    McpServer,
    McpServerDetail,
    McpTenant,
)

router = APIRouter(prefix="/openapi/v1/bots/mcp", tags=["mcp"])

#: What the *configuration* operations declare: ``REFUSED`` to a machine
#: caller. They read and write account-level state with no bot dimension — a
#: grant is consent to reach a bot, not to reconfigure an account. The
#: catalogue reads above them are OPEN and carry nothing. The refusal already
#: happens centrally in ``require_principal``; the declaration makes the
#: decision visible on the routes that carry it. See ``refuse_app_only_caller``.
_REFUSES_APP_ONLY = [Depends(refuse_app_only_caller)]

#: The path parameter naming the MCP server an operation addresses.
ServerCodePath = Annotated[
    str,
    Path(
        description="The MCP server's code, exactly as returned by the server "
        "listing — an opaque, case-sensitive identifier, e.g. "
        "'mcp.example.weather'."
    ),
]


# The caller's own identity is used as both entity id and (staff) entity type
# for the config push, mirroring the internal route's defaults and how the bots
# slice threads ``entity_id=owner_id``.
_ENTITY_TYPE = "staff"


# ── MCP Center dict → public model adapters ─────────────────────────


def _to_server(data: dict[str, Any]) -> McpServer:
    """Map one MCP Center server record to the public :class:`McpServer`."""
    return McpServer(
        server_code=data.get("serverCode") or data.get("server_code") or "",
        name=data.get("name") or "",
        description=data.get("description"),
        network_types=normalize_network_types(data),
        transport_protocol=primary_transport_protocol(data),
    )


def _to_server_detail(data: dict[str, Any]) -> McpServerDetail:
    """Map an MCP Center detail record (with tools) to :class:`McpServerDetail`."""
    base = _to_server(data)
    tools = data.get("tools")
    return McpServerDetail(
        **base.model_dump(),
        tools=tools if isinstance(tools, list) else [],
    )


def _category_label(category: Any) -> str:
    """A category's display string, tolerating dict or plain-string shapes."""
    if isinstance(category, dict):
        return category.get("name") or category.get("code") or ""
    return str(category)


def _to_tenant(data: dict[str, Any]) -> McpTenant:
    """Map one MCP Center tenant record to the public :class:`McpTenant`."""
    categories = data.get("categories") or []
    return McpTenant(
        code=data.get("code") or "",
        name=data.get("name") or "",
        categories=[_category_label(c) for c in categories if _category_label(c)],
    )


def _to_config(cfg: Any) -> McpConfig:
    """Map a :class:`~core.mcp.config_flow.UnifiedConfig` to :class:`McpConfig`.

    ``has_config`` keys off ``exists`` (a stored row is present), not the
    internal ``has_config`` flag (api_key/headers/transport present). The
    internal surface can use the narrower flag because it *also* returns a
    message that keys off ``exists``; this surface exposes only ``has_config``,
    so it must carry the present-vs-absent distinction itself. Otherwise a row
    holding just ``endpoint_env`` — or the row a successful ``endpoint_env``-only
    write just created — would report ``has_config: false``, indistinguishable
    from a server the caller never configured (the documented false case).
    """
    return McpConfig(
        server_code=cfg.server_code,
        api_key=cfg.api_key,
        endpoint_env=cfg.endpoint_env or "PROD",
        transport_protocol=cfg.transport_protocol,
        headers=cfg.headers or {},
        has_config=cfg.exists,
    )


# ── Marketplace ─────────────────────────────────────────────────────


@router.get(
    "/servers",
    response_model=Envelope[Page[McpServer]],
    # Authenticated, but not user-scoped. Declared on the route rather than
    # inherited from ``build_public_router`` so the guard is visible where the
    # operation is, and so ``test_public_routes_require_principal`` can see it:
    # that test walks each route's own dependant, which a group-level
    # dependency does not appear in.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def list_mcp_servers(
    request: Request,
    page_params: PageParamsDep,
    keyword: Annotated[
        str | None,
        Query(
            description="Filter: case-insensitive fuzzy match against the "
            "server's name and code."
        ),
    ] = None,
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> Envelope[Page[McpServer]]:
    """List marketplace MCP servers (filter by keyword, paginate)."""
    # No ``user_id``: this operation has no user dimension to scope by. The
    # marketplace catalogue is identical for every caller in the tenant.
    # An authenticated caller is still required — ``_PUBLIC_AUTH`` in
    # ``openapi_v1/__init__.py`` — it just has no user-shaped answer to give,
    # so asking the caller to name one would be asking for a value this handler
    # cannot use. See "Naming the end user" there.
    result = list_marketplace_servers(
        page=page_params.page,
        page_size=page_params.page_size,
        keyword=keyword,
        network_types=ALLOWED_NETWORK_TYPES,
        market_service=market_service,
    )
    # The list projects to McpServer, which carries no tools — so there is no
    # extInfo to strip here (that matters only on the detail path, which does
    # expose tools). Keeping tools out of the list is also what keeps it light.
    items = [
        _to_server(s) for s in (result.get("data") or []) if isinstance(s, dict)
    ]
    return page(result.get("total", len(items)), items, request)


@router.get(
    "/tenants",
    response_model=Envelope[list[McpTenant]],
    # Authenticated, not user-scoped — see /servers.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def list_mcp_tenants(
    request: Request,
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> Envelope[list[McpTenant]]:
    """List MCP tenants (the marketplace's own tenant concept)."""
    # No ``user_id`` — catalogue read, see list_mcp_servers.
    result = list_marketplace_tenants(market_service=market_service)
    tenants = [_to_tenant(t) for t in (result.get("data") or []) if isinstance(t, dict)]
    return envelope(tenants, request)


@router.get(
    "/servers/{server_code}",
    response_model=Envelope[McpServerDetail],
    # Authenticated, not user-scoped — see /servers.
    dependencies=[Depends(require_principal)],
)
@envelope_errors
async def get_mcp_server(
    server_code: ServerCodePath,
    request: Request,
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
) -> Envelope[McpServerDetail]:
    """Get an MCP server's detail (including tools).

    A missing server and one hidden by the network-type rule both raise the same
    not-found from one site, so a caller cannot tell "does not exist" from
    "exists but not visible to you".
    """
    # No ``user_id`` — catalogue read, see list_mcp_servers.
    detail = market_service.get_mcp_detail(server_code)
    if not detail or not is_network_type_visible(detail):
        raise McpServerNotFoundError(server_code)
    return envelope(_to_server_detail(strip_ext_info(detail)), request)


@router.get(
    "/servers/{server_code}/permissions",
    response_model=Envelope[McpPermission],
    responses=USER_SCOPED_403,
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def check_mcp_permission(
    server_code: ServerCodePath,
    request: Request,
    owner_id: UserIdDep,
    auth_service: MCPAuthServiceProtocol = Injected(MCPAuthServiceProtocol),
) -> Envelope[McpPermission]:
    """Report the caller's own permission for an MCP server.

    Always the caller's own permission: the required user_id must name the
    verified caller, so this cannot be pointed at someone else to probe their
    grants.

    Advisory, and fail-open by design: when the marketplace lookup errors,
    the answer reports the caller as permitted — the MCP server itself is the
    enforcement point, so a wrong "yes" during an outage costs one failed
    call, whereas failing closed would make an outage look like a permission
    revocation.
    """
    # The fail-open ruling is recorded in the MCP track's spec (Open Question
    # 1); the identity property is what the internal route's free-form user_id
    # query param does not have — the parameter states whose permission is
    # being asked about, it does not widen whose it may be.
    result = auth_service.check_mcp_permission_detail(owner_id, server_code)
    return envelope(
        McpPermission(
            has_access=bool(result.get("has_permission")),
            access_level=result.get("access_level"),
            tool_permissions=result.get("tool_permissions") or {},
        ),
        request,
    )


# ── Unified config ──────────────────────────────────────────────────


@router.get(
    "/configs",
    response_model=Envelope[Page[McpConfig]],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def list_mcp_configs(
    request: Request,
    page_params: PageParamsDep,
    owner_id: UserIdDep,
    config_service: MCPConfigServiceProtocol = Injected(MCPConfigServiceProtocol),
) -> Envelope[Page[McpConfig]]:
    """List the servers the caller has configured (api_keys masked).

    The counterpart to reading one server's config, and the only way to answer
    "what have I set up?" without already knowing every server code. Entries are
    newest-configured first.

    Masking is the same function the single-server read uses, so enumerating
    cannot reveal what reading one server protects. A caller who has configured
    nothing gets an empty page, not an error.
    """
    total, configs = list_unified_configs(
        user_id=owner_id,
        page=page_params.page,
        page_size=page_params.page_size,
        config_service=config_service,
    )
    return page(total, [_to_config(cfg) for cfg in configs], request)


@router.get(
    "/servers/{server_code}/config",
    response_model=Envelope[McpConfig],
    responses=USER_SCOPED_403,
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def get_mcp_config(
    server_code: ServerCodePath,
    request: Request,
    owner_id: UserIdDep,
    config_service: MCPConfigServiceProtocol = Injected(MCPConfigServiceProtocol),
) -> Envelope[McpConfig]:
    """Read the caller's unified config for an MCP server (api_key masked).

    A server the caller has never configured is not an error — it returns
    has_config false with defaults.
    """
    cfg = read_unified_config(
        user_id=owner_id, server_code=server_code, config_service=config_service
    )
    return envelope(_to_config(cfg), request)


@router.put(
    "/servers/{server_code}/config",
    response_model=Envelope[McpConfig],
    responses=USER_SCOPED_403,
    dependencies=_REFUSES_APP_ONLY,
)
@envelope_errors
async def update_mcp_config(
    server_code: ServerCodePath,
    body: McpConfigWrite,
    request: Request,
    owner_id: UserIdDep,
    config_service: MCPConfigServiceProtocol = Injected(MCPConfigServiceProtocol),
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
    sync_service: MCPSyncServiceProtocol = Injected(MCPSyncServiceProtocol),
) -> Envelope[McpConfig]:
    """Write the caller's unified config and push it to the caller's devices.

    A null field is left unchanged (merge, not replace). If the device push
    fails the write is rolled back and the call fails — the caller never ends up
    with a config that is stored but not in effect. The response is re-read from
    storage so it is exactly what a subsequent GET would return.
    """
    await write_unified_config(
        user_id=owner_id,
        server_code=server_code,
        entity_id=owner_id,
        entity_type=_ENTITY_TYPE,
        api_key=body.api_key,
        headers=body.headers,
        endpoint_env=body.endpoint_env,
        transport_protocol=body.transport_protocol,
        config_service=config_service,
        market_service=market_service,
        sync_service=sync_service,
    )
    cfg = read_unified_config(
        user_id=owner_id, server_code=server_code, config_service=config_service
    )
    return envelope(_to_config(cfg), request)


@router.delete(
    "/servers/{server_code}/config",
    response_model=Envelope[McpConfigDeleted],
    responses=USER_SCOPED_403,
)
@envelope_errors
async def delete_mcp_config(
    server_code: ServerCodePath,
    request: Request,
    owner_id: UserIdDep,
    config_service: MCPConfigServiceProtocol = Injected(MCPConfigServiceProtocol),
    market_service: MCPMarketServiceProtocol = Injected(MCPMarketServiceProtocol),
    sync_service: MCPSyncServiceProtocol = Injected(MCPSyncServiceProtocol),
) -> Envelope[McpConfigDeleted]:
    """Delete the caller's config for an MCP server and clear it from devices.

    The only way to *remove* a credential. The write path merges — a null field
    leaves the stored value alone — so without this a stored api_key could be
    overwritten but never taken away, and revoking a leaked key meant replacing
    it with a decoy.

    Deleting a config the caller does not have succeeds with deleted false;
    revoking twice is not an error. If the device push fails the row is restored
    and the call fails (502), so a caller never believes a credential is gone
    while an agent still holds it.

    **This does not deactivate the server on any bot.** The credential and the
    bot's use of the server are separate axes: the MCP stays where it is, now
    without a stored credential. Use the bot-scoped deactivate to turn it off.
    """
    deleted = await delete_unified_config(
        user_id=owner_id,
        server_code=server_code,
        entity_id=owner_id,
        entity_type=_ENTITY_TYPE,
        config_service=config_service,
        market_service=market_service,
        sync_service=sync_service,
    )
    return envelope(
        McpConfigDeleted(server_code=server_code, deleted=deleted), request
    )
