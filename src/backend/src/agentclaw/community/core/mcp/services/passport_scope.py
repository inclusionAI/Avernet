"""Helpers for shaping MCP scope before submitting it to the passport service."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from agentclaw.community.core.mcp.services.local_mcp_registry import LocalMCPRegistry


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


def _case_eq(value: Any, expected: str) -> bool:
    return str(value or "").casefold() == expected.casefold()
