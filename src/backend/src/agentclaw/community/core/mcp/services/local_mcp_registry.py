"""Prod-side local MCP registry backed by a repository config file."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from agentclaw.community.log import get_logger


logger = get_logger()

LOCAL_MCP_CONFIG_FILENAME = "local-mcp-servers.yaml"


class LocalMCPRegistry:
    """Reads prod-side LOCAL MCP definitions from ``configs/local-mcp-servers.yaml``.

    Supported file shape:

    ``{"servers": [{...}]}`` or a top-level list of server dicts. Each entry
    is normalized into the MCP Center-like field names used by sync/auth code.
    """

    def __init__(self, config_path: str | os.PathLike[str] | None = None) -> None:
        self._config_path = Path(config_path).expanduser() if config_path else self._default_config_path()

    def get_mcp_detail(self, server_code: str) -> dict[str, Any] | None:
        for item in self.list_mcp_details(server_codes=[server_code]):
            return item
        return None

    def list_mcp_details(
        self,
        *,
        search_key: str | None = None,
        server_codes: list[str] | None = None,
        platform_server_codes: list[str] | None = None,
        run_modes: list[str] | None = None,
        statuses: list[str] | None = None,
        transport_protocols: list[str] | None = None,
        host_platforms: list[str] | None = None,
        owners: list[str] | None = None,
        network_types: list[str] | None = None,
        categories: list[str] | None = None,
        tenants: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        items = self._load_servers()
        filtered = [
            item
            for item in items
            if self._matches_filters(
                item,
                search_key=search_key,
                server_codes=server_codes,
                platform_server_codes=platform_server_codes,
                run_modes=run_modes,
                statuses=statuses,
                transport_protocols=transport_protocols,
                host_platforms=host_platforms,
                owners=owners,
                network_types=network_types,
                categories=categories,
                tenants=tenants,
            )
        ]
        return copy.deepcopy(filtered)

    def _load_servers(self) -> list[dict[str, Any]]:
        config_path = self._resolve_config_path()
        if config_path is None:
            return []

        raw = self._load_config_file(config_path)
        servers = self._extract_servers(raw)
        normalized: list[dict[str, Any]] = []
        for item in servers:
            if not isinstance(item, dict):
                logger.warning("[LocalMCPRegistry] Skip non-dict local MCP entry: %r", item)
                continue
            detail = self._normalize_server(item)
            if detail is not None:
                normalized.append(detail)
        return normalized

    def _resolve_config_path(self) -> Path | None:
        path = self._config_path
        if not path.exists():
            logger.warning("[LocalMCPRegistry] Local MCP config does not exist: %s", path)
            return None
        if not path.is_file():
            logger.warning("[LocalMCPRegistry] Local MCP config is not a file: %s", path)
            return None
        return path

    def _default_config_path(self) -> Path:
        for parent in Path(__file__).resolve().parents:
            if (parent / "configs" / "application.yaml").exists():
                return parent / "configs" / LOCAL_MCP_CONFIG_FILENAME
        return Path("configs") / LOCAL_MCP_CONFIG_FILENAME

    def _load_config_file(self, path: Path) -> Any:
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() in {".yaml", ".yml"}:
                try:
                    import yaml
                except Exception:
                    logger.warning("[LocalMCPRegistry] PyYAML is not available for %s", path)
                    return {}
                return yaml.safe_load(text) or {}
            return json.loads(text)
        except Exception as exc:
            logger.warning("[LocalMCPRegistry] Failed to load %s: %s", path, exc)
            return {}

    def _extract_servers(self, raw: Any) -> list[Any]:
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            servers = raw.get("servers", [])
            if isinstance(servers, list):
                return servers
            if isinstance(servers, dict):
                return self._entries_from_mapping(servers)
            return []
        return []

    def _entries_from_mapping(self, servers: dict[str, Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for server_code, value in servers.items():
            if not isinstance(value, dict):
                logger.warning(
                    "[LocalMCPRegistry] Skip non-dict local MCP entry for %s: %r",
                    server_code,
                    value,
                )
                continue
            entry = copy.deepcopy(value)
            entry.setdefault("serverCode", server_code)
            entries.append(entry)
        return entries

    def _normalize_server(self, item: dict[str, Any]) -> dict[str, Any] | None:
        detail = copy.deepcopy(item)
        server_code = str(detail.get("serverCode") or detail.get("server_code") or "").strip()
        if not server_code:
            logger.warning("[LocalMCPRegistry] Skip local MCP without serverCode/server_code: %r", item)
            return None

        detail["serverCode"] = server_code
        detail["server_code"] = server_code
        detail.setdefault("name", server_code)
        detail.setdefault("description", detail.get("name", server_code))

        access_level = detail.get("accessLevel") or detail.get("access_level") or "LOCAL"
        detail["accessLevel"] = access_level
        detail["access_level"] = access_level

        run_mode = detail.get("runMode") or detail.get("run_mode") or "LOCAL"
        detail["runMode"] = run_mode
        detail["run_mode"] = run_mode

        detail.setdefault("status", "ONLINE")
        detail.setdefault("source", "local")
        detail.setdefault("tools", [])

        stdio_configs = self._normalize_stdio_configs(detail)
        if stdio_configs is not None:
            detail["stdioConfigs"] = stdio_configs
            detail["stdio_configs"] = copy.deepcopy(stdio_configs)

        return detail

    def _normalize_stdio_configs(self, detail: dict[str, Any]) -> list[Any] | None:
        stdio_configs = detail.get("stdioConfigs")
        if stdio_configs is None:
            stdio_configs = detail.get("stdio_configs")
        if stdio_configs is None and detail.get("command"):
            stdio_config: dict[str, Any] = {"command": detail["command"]}
            arguments = detail.get("arguments", detail.get("args"))
            if arguments is not None:
                stdio_config["arguments"] = arguments
            env = detail.get("envVariables", detail.get("env"))
            if env is not None:
                stdio_config["envVariables"] = env
            stdio_configs = [stdio_config]
        return copy.deepcopy(stdio_configs) if isinstance(stdio_configs, list) else None

    def _matches_filters(
        self,
        item: dict[str, Any],
        *,
        search_key: str | None,
        server_codes: list[str] | None,
        platform_server_codes: list[str] | None,
        run_modes: list[str] | None,
        statuses: list[str] | None,
        transport_protocols: list[str] | None,
        host_platforms: list[str] | None,
        owners: list[str] | None,
        network_types: list[str] | None,
        categories: list[str] | None,
        tenants: list[str] | None,
    ) -> bool:
        if server_codes and item.get("serverCode") not in set(server_codes):
            return False
        if search_key and not self._matches_search(item, search_key):
            return False
        if not self._field_in(item, ("platformServerCode", "platform_server_code"), platform_server_codes):
            return False
        if not self._field_in(item, ("runMode", "run_mode"), run_modes, casefold=True):
            return False
        if not self._field_in(item, ("status",), statuses, casefold=True):
            return False
        if not self._transport_in(item, transport_protocols):
            return False
        if not self._field_in(item, ("hostPlatform", "host_platform"), host_platforms):
            return False
        if not self._field_in(item, ("owner", "owners", "ownerName", "owner_id"), owners):
            return False
        if not self._field_in(item, ("networkType", "network_type"), network_types):
            return False
        if not self._field_in(item, ("category", "categoryCode", "category_code"), categories):
            return False
        if not self._field_in(item, ("tenantCode", "tenant_code", "tenant"), tenants):
            return False
        return True

    def _matches_search(self, item: dict[str, Any], search_key: str) -> bool:
        needle = search_key.casefold()
        haystacks = [
            item.get("serverCode"),
            item.get("server_code"),
            item.get("name"),
            item.get("description"),
        ]
        return any(
            needle in str(value).casefold()
            for value in haystacks
            if value is not None
        )

    def _field_in(
        self,
        item: dict[str, Any],
        field_names: tuple[str, ...],
        accepted: list[str] | None,
        *,
        casefold: bool = False,
    ) -> bool:
        if not accepted:
            return True
        accepted_set = {self._norm(v, casefold=casefold) for v in accepted}
        values: list[Any] = []
        for field_name in field_names:
            value = item.get(field_name)
            if isinstance(value, list):
                values.extend(value)
            elif value is not None:
                values.append(value)
        return any(self._norm(value, casefold=casefold) in accepted_set for value in values)

    def _transport_in(
        self,
        item: dict[str, Any],
        accepted: list[str] | None,
    ) -> bool:
        if not accepted:
            return True
        accepted_set = {self._norm(value, casefold=True) for value in accepted}
        protocols: list[Any] = []
        for endpoint in item.get("endpoints", []) or []:
            if isinstance(endpoint, dict) and endpoint.get("transportProtocol"):
                protocols.append(endpoint["transportProtocol"])
        if item.get("stdioConfigs") or item.get("stdio_configs"):
            protocols.append("STDIO")
        return any(self._norm(value, casefold=True) in accepted_set for value in protocols)

    def _norm(self, value: Any, *, casefold: bool) -> str:
        text = str(value)
        return text.casefold() if casefold else text
