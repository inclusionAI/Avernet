"""Deployment configuration for the Backend→BCS manager sync.

The configuration lives outside ``di/modules/config_module.py`` because
that shared module is at the repository's 1,000-line limit; the provider
that turns this into a binding lives in
``di/modules/bot_manager_sync_module.py``.

The surface is exactly six keys, and the default is **off**:

- ``enabled`` (default ``false``) — nothing about the syncer runs, and
  no thread/client exists, unless the deployment opts in.
- ``target_id`` — the fixed coordination-namespace identity (hashed into
  the Redis key layout), shared by every replica of one target. Never
  re-randomized per pod.
- ``env`` — the deployment's own environment label. The syncer serves
  exactly this env; requests naming another env are refused before any
  send (the background must not pretend to serve an env it was not
  configured for, and Git branches are *not* envs).
- ``gateway_base_url`` — the Gateway external origin. Provided by the
  deployment injection; never a source-code default, and must be an
  ``http://``/``https://`` URL.
- ``app_secret_names_by_tenant`` — Backend tenant → named secret in the
  credential registry. The map *selects* credentials by name; it never
  carries a credential value and never forges tenant identity.
- ``coordination_generation`` — the ops-initialized namespace
  generation sentinel value. Enabling without one keeps the sender
  stopped (a missing generation must stop all sends, never auto-create).

Unknown keys, wrong types, or enabled-with-missing-fields are structural
configuration errors: they raise at resolution and fail the boot on
every profile, per the repo's config policy. The Redis URL is NOT part
of this block — the shared cache configuration
(``CommunityCacheConfig``) already owns it; deployments without shared
Redis keep the feature unavailable (never a process-local lock
fallback).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

#: The yaml block name inside ``user_config``.
CONFIG_BLOCK_NAME = "bot_manager_sync"

#: Every key the block may name; anything else is an unknown-key error.
_ALLOWED_KEYS = frozenset(
    {
        "enabled",
        "target_id",
        "env",
        "gateway_base_url",
        "app_secret_names_by_tenant",
        "coordination_generation",
    }
)


@dataclass(frozen=True)
class ManagerSyncConfig:
    """Parsed ``bot_manager_sync`` block; defaults are all-off/empty."""

    enabled: bool = False
    target_id: str = ""
    env: str = ""
    gateway_base_url: str = ""
    app_secret_names_by_tenant: Mapping[str, str] = field(default_factory=dict)
    coordination_generation: str = ""


def parse_manager_sync_config(
    block: Mapping[str, object] | None,
) -> ManagerSyncConfig:
    """Strictly parse the ``bot_manager_sync`` block.

    A missing/empty block is the all-off default. Anything malformed
    raises ``ValueError`` — a structural config error, not a silent
    fallback — and enabling the feature requires every deployment-owned
    field to be present and well-formed.
    """
    if block is None:
        return ManagerSyncConfig()
    if not isinstance(block, Mapping):
        raise ValueError(f"{CONFIG_BLOCK_NAME} must be a mapping")
    unknown = sorted(set(block) - _ALLOWED_KEYS)
    if unknown:
        raise ValueError(
            f"{CONFIG_BLOCK_NAME} contains unknown keys: " + ", ".join(unknown)
        )
    for key in ("enabled",):
        if key in block and type(block[key]) is not bool:
            raise ValueError(f"{CONFIG_BLOCK_NAME}.{key} must be a boolean")
    string_fields = ("target_id", "env", "gateway_base_url", "coordination_generation")
    values: dict[str, str] = {}
    for key in string_fields:
        if key in block:
            if not isinstance(block[key], str):
                raise ValueError(f"{CONFIG_BLOCK_NAME}.{key} must be a string")
            values[key] = block[key]  # type: ignore[assignment]
        else:
            values[key] = ""
    secret_map: dict[str, str] = {}
    if "app_secret_names_by_tenant" in block:
        raw_map = block["app_secret_names_by_tenant"]
        if not isinstance(raw_map, Mapping):
            raise ValueError(
                f"{CONFIG_BLOCK_NAME}.app_secret_names_by_tenant must be a mapping"
            )
        for tenant, secret_name in raw_map.items():
            if not isinstance(tenant, str) or not tenant:
                raise ValueError(
                    f"{CONFIG_BLOCK_NAME}.app_secret_names_by_tenant keys must "
                    "be non-empty tenant strings"
                )
            if not isinstance(secret_name, str) or not secret_name.strip():
                raise ValueError(
                    f"{CONFIG_BLOCK_NAME}.app_secret_names_by_tenant values "
                    f"must be non-empty secret names (tenant {tenant!r})"
                )
            secret_map[tenant] = secret_name
    enabled = block.get("enabled", False)
    assert isinstance(enabled, bool)  # narrowed above

    if not enabled:
        # Types were validated above; a disabled block may still carry a
        # half-filled value set — but a *malformed* value must fail the
        # boot on every profile, never slide by as "off anyway".
        if values["gateway_base_url"] and not _is_http_url(values["gateway_base_url"]):
            raise ValueError(
                f"{CONFIG_BLOCK_NAME}.gateway_base_url must be an http:// or "
                "https:// URL"
            )
        return ManagerSyncConfig(
            enabled=False,
            target_id=values["target_id"],
            env=values["env"],
            gateway_base_url=values["gateway_base_url"],
            app_secret_names_by_tenant=secret_map,
            coordination_generation=values["coordination_generation"],
        )

    # Enabled: every deployment-owned field is required, and none may be
    # an indistinct empty string — a missing value here is structural.
    required = {
        "target_id": values["target_id"],
        "env": values["env"],
        "gateway_base_url": values["gateway_base_url"],
        "coordination_generation": values["coordination_generation"],
    }
    missing = sorted(name for name, value in required.items() if not value.strip())
    if not secret_map:
        missing.append("app_secret_names_by_tenant")
    if missing:
        raise ValueError(
            f"{CONFIG_BLOCK_NAME} enabled requires these fields: "
            + ", ".join(missing)
        )
    if not _is_http_url(required["gateway_base_url"]):
        raise ValueError(
            f"{CONFIG_BLOCK_NAME}.gateway_base_url must be an http:// or "
            "https:// URL"
        )
    return ManagerSyncConfig(
        enabled=True,
        target_id=required["target_id"],
        env=required["env"],
        gateway_base_url=required["gateway_base_url"],
        app_secret_names_by_tenant=secret_map,
        coordination_generation=required["coordination_generation"],
    )


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


__all__ = [
    "CONFIG_BLOCK_NAME",
    "ManagerSyncConfig",
    "parse_manager_sync_config",
]