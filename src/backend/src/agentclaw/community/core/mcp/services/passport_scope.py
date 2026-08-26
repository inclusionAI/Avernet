"""Helpers for shaping MCP scope before submitting it to the passport service."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agentclaw.community.core.mcp.services.local_mcp_registry import LocalMCPRegistry
from agentclaw.community.plugin_api.passport import CliItem, McpScopeItem


def merge_passport_cli_items(
    current: list[CliItem] | None,
    defaults: list[CliItem] | None,
) -> list[CliItem]:
    """Merge Passport CLI scope and defaults, de-duplicated by cli_code.

    Passport replaces the complete resource manifest. Existing CLI items win
    over defaults so a scope refresh cannot clear a user's CLI metadata.
    """
    merged: list[CliItem] = []
    seen: set[str] = set()
    for item in (current or []) + (defaults or []):
        if not isinstance(item, dict):
            continue
        cli_code = item.get("cli_code")
        if not cli_code or cli_code in seen:
            continue
        seen.add(cli_code)
        merged.append(dict(item))
    return merged


def filter_passport_mcp_codes(
    mcp_codes: Iterable[str],
    *,
    local_registry: LocalMCPRegistry | None = None,
) -> list[str]:
    """Return MCP codes that should be declared to the passport service.

    LOCAL/stdio MCP servers are runtime-local capabilities. They still need to
    be synced to the device, but the passport service does not own their permission scope.
    """
    local_codes = _local_mcp_codes(local_registry)
    return [
        code
        for code in mcp_codes
        if code and not _is_local_mcp_code(code, local_codes)
    ]


def passport_mcp_codes_from_entries(
    mcps: Iterable[Mapping[str, Any]],
    *,
    local_registry: LocalMCPRegistry | None = None,
) -> list[str]:
    """Extract passport-declared MCP codes from MCP entries, excluding LOCAL/stdio."""
    local_codes = _local_mcp_codes(local_registry)
    codes: list[str] = []
    for mcp in mcps:
        code = _server_code(mcp)
        if code and not _is_local_mcp_entry(mcp, code, local_codes):
            codes.append(code)
    return codes


def normalized_identity_mode(raw: object) -> str:
    """Normalize one stored execution identity to the wire vocabulary.

    Shared by both scope builders so the identity contract has exactly one
    definition: an unrecognised value is rejected here rather than reaching
    the Passport port, which would otherwise coerce it or fail further from
    the cause.
    """
    mode = getattr(raw, "value", raw)
    normalized_mode = str(mode).strip().lower()
    if normalized_mode not in {"owner", "caller"}:
        raise ValueError("identity mode must be owner or caller")
    return normalized_mode


def passport_mcp_items_from_codes(
    mcp_codes: Iterable[str],
    *,
    identity_modes: Mapping[str, object],
) -> list[McpScopeItem]:
    """Build the MCP identity scope from codes that are already filtered.

    The entries variant below is for callers holding full MCP payloads. This
    one is for callers that already resolved their non-local codes — a
    runtime projection, say — and would otherwise have to re-derive the
    identity default and its validation, which is how the two copies drift.

    ``mcp_name`` / ``mcp_desc`` are omitted deliberately: the Passport port
    leaves them off the wire when absent, so identity can be declared
    without an MCP-Center round trip per code.
    """
    return [
        {
            "mcp_code": code,
            "identity_mode": normalized_identity_mode(identity_modes.get(code, "owner")),
        }
        for code in mcp_codes
    ]


def passport_mcp_items_from_entries(
    mcps: Iterable[Mapping[str, Any]],
    *,
    identity_modes: Mapping[str, object],
    local_registry: LocalMCPRegistry | None = None,
) -> list[McpScopeItem]:
    """Build the complete non-local MCP identity scope for Agent Principal."""
    local_codes = _local_mcp_codes(local_registry)
    items: list[McpScopeItem] = []
    for mcp in mcps:
        code = _server_code(mcp)
        if not code or _is_local_mcp_entry(mcp, code, local_codes):
            continue
        normalized_mode = normalized_identity_mode(identity_modes.get(code, "owner"))
        items.append({
            "mcp_code": code,
            "mcp_name": _optional_text(
                mcp.get("name") or mcp.get("serverName") or mcp.get("server_name")
            ),
            "mcp_desc": _optional_text(
                mcp.get("description") or mcp.get("serverDescription")
                or mcp.get("server_description")
            ),
            "identity_mode": normalized_mode,
        })
    return items


def _local_mcp_codes(local_registry: LocalMCPRegistry | None) -> set[str]:
    registry = local_registry or LocalMCPRegistry()
    return {
        str(item.get("serverCode") or item.get("server_code"))
        for item in registry.list_mcp_details()
        if item.get("serverCode") or item.get("server_code")
    }


def _is_local_mcp_entry(
    mcp: Mapping[str, Any],
    code: str,
    local_codes: set[str],
) -> bool:
    return (
        _case_eq(mcp.get("source"), "local")
        or _case_eq(mcp.get("accessLevel") or mcp.get("access_level"), "LOCAL")
        or _case_eq(mcp.get("runMode") or mcp.get("run_mode"), "LOCAL")
        or bool(mcp.get("stdioConfigs") or mcp.get("stdio_configs"))
        or _is_local_mcp_code(code, local_codes)
    )


def _is_local_mcp_code(code: str, local_codes: set[str]) -> bool:
    return code in local_codes


def _server_code(mcp: Mapping[str, Any]) -> str:
    return str(mcp.get("server_code") or mcp.get("serverCode") or "").strip()


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _case_eq(value: Any, expected: str) -> bool:
    return str(value or "").casefold() == expected.casefold()
