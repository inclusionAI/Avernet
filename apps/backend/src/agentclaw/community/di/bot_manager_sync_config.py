"""Strict, default-off configuration for best-effort Backend → BCS sync.

Authentication uses the existing gateway-principal signing-key configuration.
There is no separate sync credential, coordination store or persistence mode.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

CONFIG_BLOCK_NAME = "bot_manager_sync"
_ALLOWED_KEYS = frozenset({"enabled", "env", "bcs_base_url"})


@dataclass(frozen=True)
class ManagerSyncConfig:
    enabled: bool = False
    env: str = ""
    bcs_base_url: str = ""


def parse_manager_sync_config(block: Mapping[str, object] | None) -> ManagerSyncConfig:
    """Reject unknown/malformed values even when disabled; require target when on."""
    if block is None:
        return ManagerSyncConfig()
    if not isinstance(block, Mapping):
        raise ValueError(f"{CONFIG_BLOCK_NAME} must be a mapping")
    unknown = set(block) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"{CONFIG_BLOCK_NAME} contains unknown keys: {sorted(unknown, key=str)}")
    enabled = block.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError(f"{CONFIG_BLOCK_NAME}.enabled must be a boolean")
    values: dict[str, str] = {}
    for field in ("env", "bcs_base_url"):
        value = block.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"{CONFIG_BLOCK_NAME}.{field} must be a string")
        values[field] = value.strip()
        if enabled and not values[field]:
            raise ValueError(f"{CONFIG_BLOCK_NAME} enabled requires {field}")
    if values["bcs_base_url"]:
        if not _is_origin(values["bcs_base_url"]):
            raise ValueError(f"{CONFIG_BLOCK_NAME}.bcs_base_url must be an http(s) origin")
    return ManagerSyncConfig(enabled=enabled, **values)


def _is_origin(value: str) -> bool:
    """Only a service origin; the HTTP adapter owns the fixed API path."""
    try:
        parsed = urlparse(value)
        port = parsed.port  # malformed/out-of-range ports fail before startup
        return (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not any(character.isspace() for character in parsed.netloc)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
            and (port is None or port > 0)
        )
    except ValueError:
        return False
