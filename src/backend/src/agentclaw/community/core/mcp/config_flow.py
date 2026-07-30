"""Request-agnostic MCP unified-config read/write orchestration.

Both API surfaces need the same multi-step config write — validate the values
and headers, confirm the server exists *before* any database write, persist the
row keeping the old one for rollback, push to every device under the identity,
and roll back if that push fails. This module owns that sequence so the internal
``/api/mcp`` router and the public ``/openapi/v1/bots/mcp`` router call one
implementation instead of each carrying a copy — the pattern
``core/bot_management/create_flow.py`` established for bot creation.

The functions here are FastAPI-free: they take the resolved caller identity plus
the already-injected services, return a :class:`UnifiedConfig` whose ``api_key``
is **already masked**, and **raise** the ``core/mcp/errors.py`` domain errors for
each surface to map onto its own response shape.

The write result is shaped to match the internal surface's historical write
response (which does not echo ``headers`` and reports the request's own values),
so the extraction is behavior-preserving; the public surface re-reads via
:func:`read_unified_config` for a response consistent with a subsequent GET.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.mcp.errors import (
    McpConfigValueError,
    McpHeadersInvalidError,
    McpMarketUnavailableError,
    McpServerNotFoundError,
    McpSyncFailedError,
)
from agentclaw.community.core.mcp.presentation import mask_api_key

_VALID_ENDPOINT_ENVS = ("PROD", "PRE")
_VALID_TRANSPORT_PROTOCOLS = ("SSE", "STREAMABLE_HTTP")


@dataclass(frozen=True)
class UnifiedConfig:
    """A caller's unified config for one MCP server, ready to serialize.

    ``api_key`` is already masked — no caller can reach the raw key through this
    object. ``sync_results`` is populated only by :func:`write_unified_config`
    (the raw per-bot push outcomes); reads leave it ``None``.
    """

    server_code: str
    api_key: str | None
    endpoint_env: str | None
    transport_protocol: str | None
    headers: dict[str, str] | None
    has_config: bool
    # Whether a stored row exists at all — distinct from ``has_config`` (a row
    # can exist while carrying no api_key/headers/transport). The internal GET
    # message ("Config retrieved" vs "No config found") keys off this, not
    # ``has_config``. Always ``True`` for a write result.
    exists: bool = field(default=True)
    sync_results: list[dict[str, Any]] | None = field(default=None)


def _validate_endpoint_env(endpoint_env: str | None) -> None:
    """Reject an endpoint env outside the accepted set. ``None`` = leave unchanged."""
    if endpoint_env is not None and endpoint_env not in _VALID_ENDPOINT_ENVS:
        raise McpConfigValueError("endpoint_env must be PROD or PRE")


def _normalize_transport_protocol(transport_protocol: str | None) -> str | None:
    """Upper-case and validate the transport protocol. ``None`` = leave unchanged.

    Upper-casing matches the internal route, which normalized before validating;
    it is idempotent, so the public surface's already-strict value is unaffected.
    """
    if transport_protocol is None:
        return None
    normalized = transport_protocol.upper()
    if normalized not in _VALID_TRANSPORT_PROTOCOLS:
        raise McpConfigValueError("transport_protocol must be SSE or STREAMABLE_HTTP")
    return normalized


def read_unified_config(
    *, user_id: str, server_code: str, config_service: Any
) -> UnifiedConfig:
    """Read a caller's unified config for one server (never raises for "absent").

    A server the caller has never configured returns a :class:`UnifiedConfig`
    with ``has_config`` false and the defaults (``endpoint_env="PROD"``, empty
    headers) — not an error — so "no config" is distinguishable from a failure.
    """
    config = config_service.get_user_unified_config(user_id, server_code)
    if not config:
        return UnifiedConfig(
            server_code=server_code,
            api_key=None,
            endpoint_env="PROD",
            transport_protocol=None,
            headers={},
            has_config=False,
            exists=False,
        )
    api_key = config.get("api_key")
    return UnifiedConfig(
        server_code=server_code,
        api_key=mask_api_key(api_key),
        endpoint_env=config.get("endpoint_env", "PROD"),
        transport_protocol=config.get("transport_protocol"),
        headers=config.get("headers", {}),
        has_config=bool(
            api_key or config.get("headers") or config.get("transport_protocol")
        ),
    )


async def write_unified_config(
    *,
    user_id: str,
    server_code: str,
    entity_id: str,
    entity_type: str,
    api_key: str | None,
    headers: dict[str, str] | None,
    endpoint_env: str | None,
    transport_protocol: str | None,
    config_service: Any,
    market_service: Any,
    sync_service: Any,
) -> UnifiedConfig:
    """Persist a caller's config and push it to their devices, atomically.

    Order is load-bearing and preserved from the internal route:

    1. Validate the values (``endpoint_env`` / ``transport_protocol``).
    2. Validate headers when present — this call also confirms the server exists,
       so a missing server with headers present surfaces as an invalid-headers
       error (pre-existing quirk, kept).
    3. Confirm the server exists via the marketplace, **before** any write, so a
       bad server code never touches the database.
    4. Write the row, keeping the previous config for rollback.
    5. Push to every device under the identity.
    6. If the push fails, roll the write back and raise — the caller never ends
       up with a config that is stored but not in effect.

    Returns a write-shaped :class:`UnifiedConfig`: ``headers`` is ``None`` (the
    write response has never echoed them) and the value fields reflect the
    request, mirroring the internal surface. ``sync_results`` carries the raw
    per-bot outcomes for the internal response to map.
    """
    _validate_endpoint_env(endpoint_env)
    normalized_tp = _normalize_transport_protocol(transport_protocol)

    if headers is not None:
        validation = config_service.validate_headers_for_mcp(server_code, headers)
        if not validation["valid"]:
            raise McpHeadersInvalidError(validation["error"])

    mcp_data = market_service.get_mcp_detail(server_code)
    if not mcp_data:
        raise McpServerNotFoundError(server_code)

    old_config = config_service.update_user_unified_config(
        user_id=user_id,
        server_code=server_code,
        api_key=api_key,
        headers=headers,
        endpoint_env=endpoint_env,
        transport_protocol=normalized_tp,
    )

    result = await sync_service.sync_mcp_detail_to_all_bots(
        user_id=user_id,
        server_code=server_code,
        mcp_data=mcp_data,
        entity_id=entity_id,
        entity_type=entity_type,
        api_key=api_key,
        custom_headers=headers,
        endpoint_env=endpoint_env,
        transport_protocol=normalized_tp,
    )

    if not result["success"]:
        config_service.rollback_unified_config(
            user_id=user_id,
            server_code=server_code,
            old_config=old_config,
        )
        # Preserve the internal surface's exact 500 detail: the literal fallback
        # and ``.get(key, default)`` semantics (default only when the key is
        # absent). The public surface maps this error to a fixed message and
        # never reads str(exc), so carrying the internal text here costs it
        # nothing. The real sync service always sets a non-empty ``error`` on
        # failure, so the fallback is defense-in-depth.
        raise McpSyncFailedError(
            result.get("error", "Failed to sync to all devices")
        )

    return UnifiedConfig(
        server_code=server_code,
        api_key=mask_api_key(api_key),
        endpoint_env=endpoint_env,
        transport_protocol=normalized_tp,
        headers=None,  # write path has never echoed headers — read path does
        has_config=bool(api_key or headers or transport_protocol),
        sync_results=result.get("sync_results"),
    )


def list_marketplace_servers(
    *,
    page: int,
    page_size: int,
    keyword: str | None,
    network_types: tuple[str, ...],
    market_service: Any,
) -> dict[str, Any]:
    """List marketplace servers, raising on an upstream failure.

    A marketplace call that reports ``success: False`` is an upstream problem —
    :class:`McpMarketUnavailableError`, not an empty page.
    """
    result = market_service.get_mcp_list(
        page_num=page,
        page_size=page_size,
        search_key=keyword,
        network_types=list(network_types),
    )
    if not result.get("success"):
        raise McpMarketUnavailableError(result.get("message") or "marketplace error")
    return result


def list_marketplace_tenants(*, market_service: Any) -> dict[str, Any]:
    """List MCP tenants, raising :class:`McpMarketUnavailableError` on failure."""
    result = market_service.get_tenant_list()
    if not result.get("success"):
        raise McpMarketUnavailableError(result.get("message") or "marketplace error")
    return result
