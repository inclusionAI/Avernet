"""Request-agnostic read/write flow for scoped MCP Header groups."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.mcp.config_flow import (
    _normalize_transport_protocol,
    _validate_endpoint_env,
)
from agentclaw.community.core.mcp.errors import (
    McpConfigValueError,
    McpMarketUnavailableError,
    McpServerNotFoundError,
)


@dataclass(frozen=True)
class HeaderGroup:
    key: str
    value: str
    bots: tuple[str, ...]


@dataclass(frozen=True)
class ScopedMCPConfig:
    server_code: str
    endpoint_env: str
    transport_protocol: str | None
    params: tuple[HeaderGroup, ...]
    sync_results: tuple[dict[str, Any], ...] | None = None
    sync_summary: dict[str, int] | None = None


def read_scoped_config(
    *,
    user_id: str,
    server_code: str,
    config_service: Any,
    bot_config_repo: Any,
    bot_repo: Any,
) -> ScopedMCPConfig:
    """Return only explicitly stored rules, not inherited Bot copies."""
    user_config = config_service.get_user_unified_config(user_id, server_code) or {}
    user_headers = user_config.get("headers") or {}
    params = [
        HeaderGroup(key=key, value=value, bots=())
        for key, value in sorted(user_headers.items(), key=lambda item: item[0].lower())
    ]

    owned_ids = {str(bot_id) for bot_id in bot_repo.list_live_bot_ids_by_owner(user_id)}
    bot_configs = bot_config_repo.list_by_owner_and_server_code(
        owner_id=user_id, server_code=server_code
    )
    grouped: dict[tuple[str, str], list[str]] = {}
    for bot_id in sorted(owned_ids & bot_configs.keys()):
        for key, value in (bot_configs[bot_id].get("headers") or {}).items():
            grouped.setdefault((key, value), []).append(bot_id)
    params.extend(
        HeaderGroup(key=key, value=value, bots=tuple(bot_ids))
        for (key, value), bot_ids in sorted(
            grouped.items(), key=lambda item: (item[0][0].lower(), item[0][1])
        )
    )
    return ScopedMCPConfig(
        server_code=server_code,
        endpoint_env=user_config.get("endpoint_env") or "PROD",
        transport_protocol=user_config.get("transport_protocol"),
        params=tuple(params),
    )


def _compile_groups(
    params: tuple[HeaderGroup, ...], owned_ids: set[str]
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    user_headers: dict[str, str] = {}
    bot_headers: dict[str, dict[str, str]] = {}
    user_names: set[str] = set()
    bot_names: dict[str, set[str]] = {}
    for group in params:
        name = group.key.strip()
        if not name or len(name) > 256 or len(group.value) > 2000:
            raise McpConfigValueError("Invalid MCP Header name or value")
        lowered = name.lower()
        if not group.bots:
            if lowered in user_names:
                raise McpConfigValueError(f"Duplicate user Header: {name}")
            user_names.add(lowered)
            user_headers[name] = group.value
            continue
        if len(set(group.bots)) != len(group.bots):
            raise McpConfigValueError(f"Duplicate Bot in Header group: {name}")
        for bot_id in group.bots:
            if bot_id not in owned_ids:
                raise McpConfigValueError(f"Bot is not owned by caller: {bot_id}")
            names = bot_names.setdefault(bot_id, set())
            if lowered in names:
                raise McpConfigValueError(f"Overlapping Bot Header: {name}")
            names.add(lowered)
            bot_headers.setdefault(bot_id, {})[name] = group.value
    return user_headers, bot_headers


def _validate_center_selection(
    detail: dict[str, Any], endpoint_env: str, protocol: str | None
) -> None:
    endpoints = detail.get("endpoints") or []
    if isinstance(endpoints, str):
        try:
            endpoints = json.loads(endpoints)
        except json.JSONDecodeError as exc:
            raise McpConfigValueError("MCP Center endpoints are invalid") from exc
    if not isinstance(endpoints, list) or not any(
        isinstance(endpoint, dict)
        and endpoint.get("env") == endpoint_env
        and endpoint.get("networkType") in {"OFFICE", "INTERNET", "INTRANET"}
        and (
            protocol is None or endpoint.get("transportProtocol") == protocol
        )
        for endpoint in endpoints
    ):
        raise McpConfigValueError(
            f"MCP has no {endpoint_env}/{protocol or 'default'} Center endpoint"
        )


def _projection_summary(results: tuple[dict[str, Any], ...]) -> dict[str, int]:
    return {
        "affected_bot_count": len(results),
        "synced_count": sum(1 for item in results if item.get("synced")),
        "offline_count": sum(1 for item in results if item.get("reason") == "设备离线"),
        "runtime_drift_count": sum(
            1 for item in results if item.get("reason") == "RUNTIME_DRIFT"
        ),
        "failed_count": sum(
            1 for item in results
            if not item.get("synced") and item.get("reason") != "设备离线"
        ),
    }


async def write_scoped_config(
    *,
    user_id: str,
    server_code: str,
    endpoint_env: str,
    transport_protocol: str | None,
    params: tuple[HeaderGroup, ...],
    config_service: Any,
    bot_config_repo: Any,
    bot_repo: Any,
    command_repo: Any,
    market_service: Any,
    sync_service: Any,
    capability_reader: Any,
) -> ScopedMCPConfig:
    """Validate, atomically persist, then best-effort project one snapshot."""
    _validate_endpoint_env(endpoint_env)
    normalized_protocol = _normalize_transport_protocol(transport_protocol)
    try:
        detail = market_service.get_mcp_detail(server_code)
    except Exception as exc:
        raise McpMarketUnavailableError("MCP Center unavailable") from exc
    if not detail:
        raise McpServerNotFoundError(server_code)
    if (detail.get("runMode") or detail.get("run_mode")) == "LOCAL":
        raise McpConfigValueError("LOCAL MCP does not accept remote Headers")
    _validate_center_selection(detail, endpoint_env, normalized_protocol)

    owned_ids = {str(bot_id) for bot_id in bot_repo.list_live_bot_ids_by_owner(user_id)}
    user_headers, bot_headers = _compile_groups(params, owned_ids)
    header_validation = config_service.validate_scoped_headers(
        server_code=server_code,
        entries=tuple((group.key, group.value) for group in params),
    )
    if not header_validation["valid"]:
        raise McpConfigValueError(header_validation["error"])
    existing_user = config_service.get_user_unified_config(user_id, server_code) or {}
    candidate_user = {
        **existing_user,
        "headers": user_headers,
        "endpoint_env": endpoint_env,
        "transport_protocol": normalized_protocol,
    }
    existing_bots = bot_config_repo.list_by_owner_and_server_code(
        owner_id=user_id, server_code=server_code
    )
    consumers: list[str] = []
    for bot_id in sorted(owned_ids):
        bot = bot_repo.get_by_id_and_owner(bot_id, user_id)
        if bot is None:
            continue
        candidate_bot = dict(existing_bots.get(bot_id) or {})
        if bot_headers.get(bot_id):
            candidate_bot["headers"] = bot_headers[bot_id]
        else:
            candidate_bot.pop("headers", None)
        validation = config_service.validate_effective_scoped_config(
            server_code=server_code,
            detail=detail,
            bot_config=candidate_bot,
            user_config=candidate_user,
            engine_type=bot.get("active_engine") or bot.get("engine"),
        )
        if not validation["valid"]:
            raise McpConfigValueError(f"Bot {bot_id}: {validation['error']}")
        if server_code in capability_reader.effective_mcp_server_codes(
            bot_id=bot_id, owner_id=user_id, bot=bot
        ):
            consumers.append(bot_id)

    command_repo.replace(
        user_id=user_id,
        server_code=server_code,
        headers=user_headers,
        bot_headers=bot_headers,
        owned_bot_ids=owned_ids,
        endpoint_env=endpoint_env,
        transport_protocol=normalized_protocol,
    )

    sync_results: tuple[dict[str, Any], ...] = ()
    sync_summary = _projection_summary(sync_results)
    if consumers:
        try:
            projection = await sync_service.sync_mcp_detail_to_all_bots(
                user_id=user_id,
                server_code=server_code,
                mcp_data=detail,
                entity_id=user_id,
                entity_type="staff",
                target_bot_ids=consumers,
                target_bot_owners={bot_id: user_id for bot_id in consumers},
            )
            sync_results = tuple(projection.get("sync_results") or ())
            if not projection.get("success") and not sync_results:
                sync_results = tuple(
                    {"bot_id": bot_id, "synced": False, "reason": "BATCH_FAILED"}
                    for bot_id in consumers
                )
            sync_summary = projection.get("sync_summary") or _projection_summary(sync_results)
        except Exception as exc:
            sync_results = tuple(
                {"bot_id": bot_id, "synced": False, "error": type(exc).__name__}
                for bot_id in consumers
            )
            sync_summary = _projection_summary(sync_results)

    saved = read_scoped_config(
        user_id=user_id,
        server_code=server_code,
        config_service=config_service,
        bot_config_repo=bot_config_repo,
        bot_repo=bot_repo,
    )
    return ScopedMCPConfig(
        server_code=saved.server_code,
        endpoint_env=saved.endpoint_env,
        transport_protocol=saved.transport_protocol,
        params=saved.params,
        sync_results=sync_results,
        sync_summary=sync_summary,
    )
