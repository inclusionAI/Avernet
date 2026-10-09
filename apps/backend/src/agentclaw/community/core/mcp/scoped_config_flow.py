"""Request-agnostic read/write flow for scoped MCP Header and URL rules."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from injector import inject

from agentclaw.community.core.mcp.mcp_config_service_protocol import MCPConfigServiceProtocol
from agentclaw.community.core.mcp.mcp_market_service_protocol import MCPMarketServiceProtocol
from agentclaw.community.core.mcp.mcp_sync_service_protocol import MCPSyncServiceProtocol
from agentclaw.community.core.mcp.effective_mcp_state_reader_protocol import (
    EffectiveMCPStateReaderProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotMCPConfigRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.bot.mcp import (
    ScopedMCPConfigRepositoryProtocol,
)

from agentclaw.community.core.mcp.config_flow import (
    _normalize_transport_protocol,
    _validate_endpoint_env,
)
from agentclaw.community.core.mcp.errors import (
    McpConfigValueError,
    McpMarketUnavailableError,
    McpServerNotFoundError,
)
from agentclaw.community.core.mcp.scoped_config_contract import (
    HeaderGroup,
    MCPScopedConfigServiceProtocol,
    ScopedMCPConfig,
    URLRule,
)


def read_scoped_config(
    *,
    user_id: str,
    server_code: str,
    config_service: MCPConfigServiceProtocol,
    bot_config_repo: BotMCPConfigRepositoryProtocol,
    bot_repo: BotRepository,
) -> ScopedMCPConfig:
    """Return only explicitly stored rules, not inherited Bot copies."""
    stored_default = config_service.get_user_unified_config(user_id, server_code) or {}
    user_headers = stored_default.get("headers") or {}
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
    url_rules: list[URLRule] = []
    user_url = stored_default.get("url")
    if isinstance(user_url, str) and user_url:
        url_rules.append(URLRule(url=user_url, bots=()))
    grouped_urls: dict[str, list[str]] = {}
    for bot_id in sorted(owned_ids & bot_configs.keys()):
        bot_url = bot_configs[bot_id].get("url")
        if isinstance(bot_url, str) and bot_url:
            grouped_urls.setdefault(bot_url, []).append(bot_id)
    url_rules.extend(
        URLRule(url=url, bots=tuple(bot_ids))
        for url, bot_ids in sorted(grouped_urls.items())
    )
    return ScopedMCPConfig(
        server_code=server_code,
        endpoint_env=stored_default.get("endpoint_env") or "PROD",
        transport_protocol=stored_default.get("transport_protocol"),
        params=tuple(params),
        url_rules=tuple(url_rules),
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


def _compile_url_rules(
    rules: tuple[URLRule, ...], owned_ids: set[str]
) -> tuple[str | None, dict[str, str]]:
    user_url: str | None = None
    bot_urls: dict[str, str] = {}
    for rule in rules:
        url = rule.url
        try:
            parsed = urlsplit(url)
            valid = (
                url == url.strip()
                and not any(char.isspace() or ord(char) < 32 for char in url)
                and parsed.scheme.lower() in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.port != 0
            )
        except ValueError:
            valid = False
        if not valid:
            raise McpConfigValueError("Invalid MCP URL: expected an HTTP(S) address")
        if not rule.bots:
            if user_url is not None:
                raise McpConfigValueError("Duplicate user-default MCP URL")
            user_url = url
            continue
        if len(set(rule.bots)) != len(rule.bots):
            raise McpConfigValueError("Duplicate Bot in MCP URL rule")
        for bot_id in rule.bots:
            if bot_id not in owned_ids:
                raise McpConfigValueError(f"Bot is not owned by caller: {bot_id}")
            if bot_id in bot_urls:
                raise McpConfigValueError(f"Overlapping Bot MCP URL: {bot_id}")
            bot_urls[bot_id] = url
    return user_url, bot_urls


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
    url_rules: tuple[URLRule, ...] | None = None,
    config_service: MCPConfigServiceProtocol,
    bot_config_repo: BotMCPConfigRepositoryProtocol,
    bot_repo: BotRepository,
    command_repo: ScopedMCPConfigRepositoryProtocol,
    market_service: MCPMarketServiceProtocol,
    sync_service: MCPSyncServiceProtocol,
    capability_reader: EffectiveMCPStateReaderProtocol,
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
    user_url, bot_urls = (
        _compile_url_rules(url_rules, owned_ids)
        if url_rules is not None
        else (None, None)
    )
    header_validation = config_service.validate_scoped_headers(
        server_code=server_code,
        entries=tuple((group.key.strip(), group.value) for group in params),
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
    if bot_urls is not None:
        if user_url is None:
            candidate_user.pop("url", None)
        else:
            candidate_user["url"] = user_url
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
        if bot_urls is not None:
            if bot_id in bot_urls:
                candidate_bot["url"] = bot_urls[bot_id]
            else:
                candidate_bot.pop("url", None)
        is_consumer = server_code in capability_reader.effective_mcp_server_codes(
            bot_id=bot_id, owner_id=user_id, bot=bot
        )
        if is_consumer or bot_id in bot_headers or (
            bot_urls is not None and bot_id in bot_urls
        ):
            validation = config_service.validate_effective_scoped_config(
                server_code=server_code,
                detail=detail,
                bot_config=candidate_bot,
                user_config=candidate_user,
                engine_type=bot.get("active_engine") or bot.get("engine"),
            )
            if not validation["valid"]:
                raise McpConfigValueError(f"Bot {bot_id}: {validation['error']}")
        if is_consumer:
            consumers.append(bot_id)

    command_repo.replace(
        user_id=user_id,
        server_code=server_code,
        headers=user_headers,
        bot_headers=bot_headers,
        owned_bot_ids=owned_ids,
        endpoint_env=endpoint_env,
        transport_protocol=normalized_protocol,
        user_url=user_url,
        bot_urls=bot_urls,
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
        url_rules=saved.url_rules,
        sync_results=sync_results,
        sync_summary=sync_summary,
    )


class MCPScopedConfigService(MCPScopedConfigServiceProtocol):
    """One typed, transport-independent entry point for both HTTP adapters."""

    @inject
    def __init__(
        self,
        config_service: MCPConfigServiceProtocol,
        bot_config_repo: BotMCPConfigRepositoryProtocol,
        bot_repo: BotRepository,
        command_repo: ScopedMCPConfigRepositoryProtocol,
        market_service: MCPMarketServiceProtocol,
        sync_service: MCPSyncServiceProtocol,
        capability_reader: EffectiveMCPStateReaderProtocol,
    ) -> None:
        self._config_service = config_service
        self._bot_config_repo = bot_config_repo
        self._bot_repo = bot_repo
        self._command_repo = command_repo
        self._market_service = market_service
        self._sync_service = sync_service
        self._capability_reader = capability_reader

    def read(self, *, user_id: str, server_code: str) -> ScopedMCPConfig:
        return read_scoped_config(
            user_id=user_id,
            server_code=server_code,
            config_service=self._config_service,
            bot_config_repo=self._bot_config_repo,
            bot_repo=self._bot_repo,
        )

    async def replace(
        self,
        *,
        user_id: str,
        server_code: str,
        endpoint_env: str,
        transport_protocol: str | None,
        params: tuple[HeaderGroup, ...],
        url_rules: tuple[URLRule, ...] | None = None,
    ) -> ScopedMCPConfig:
        return await write_scoped_config(
            user_id=user_id,
            server_code=server_code,
            endpoint_env=endpoint_env,
            transport_protocol=transport_protocol,
            params=params,
            url_rules=url_rules,
            config_service=self._config_service,
            bot_config_repo=self._bot_config_repo,
            bot_repo=self._bot_repo,
            command_repo=self._command_repo,
            market_service=self._market_service,
            sync_service=self._sync_service,
            capability_reader=self._capability_reader,
        )
