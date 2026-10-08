"""Config-driven privileged-user (admin) scope lists.

Each privileged scope — super-admin, disk-usage admin, harness admin, skill
admin — is a per-deployment allow-list of staff IDs sourced from the ``admin``
block of ``user_config``. The IDs are personnel identifiers and are never baked
into source: the community distribution ships them empty (every scope denies
until an operator populates it), and a corp deployment supplies them via its
private config overlay.

This lives in ``core/access`` (the permission-evaluation domain) so both the
HTTP routers (adapters → core) and core services (core → core) resolve the same
lists from one source. The read mirrors the token fix's ``_uct_auth_header``:
defensive, returning an empty frozenset on any failure (config may be absent in
bare unit tests / early boot) — fail-closed, never granting on error.
"""
from __future__ import annotations

import logging
from typing import FrozenSet

logger = logging.getLogger(__name__)


def _scope(key: str) -> FrozenSet[str]:
    """Return the configured allow-list for one admin scope.

    Reads ``user_config.admin.<key>``. Returns an empty frozenset when the block
    is missing, the value is unset/blank, not a list, or config is unavailable.
    """
    try:
        from agentclaw.community.core.config.sofa import sofa_config

        block = (getattr(sofa_config, "user_config", None) or {}).get("admin") or {}
        ids = block.get(key)
    except Exception as exc:  # defensive: config absent/malformed → deny (fail-closed)
        logger.warning("admin scope %s unavailable from config: %s", key, exc)
        return frozenset()
    if isinstance(ids, (list, tuple, set, frozenset)):
        return frozenset(str(i) for i in ids)
    return frozenset()


def super_admin() -> FrozenSet[str]:
    """Staff IDs allowed to perform cross-user bot management operations."""
    return _scope("super_admin")


def disk_usage_admin() -> FrozenSet[str]:
    """Staff IDs allowed to query/manage system disk-usage endpoints."""
    return _scope("disk_usage_admin")


def harness_admin() -> FrozenSet[str]:
    """Staff IDs allowed to call harness admin endpoints."""
    return _scope("harness_admin")


def skill_admin() -> FrozenSet[str]:
    """Staff IDs allowed privileged skill operations (MCP deps / force delete)."""
    return _scope("skill_admin")


def collaborator_admin() -> FrozenSet[str]:
    """Staff IDs allowed to add collaborators on another owner's bot."""
    return _scope("collaborator_admin")


def device_admin() -> FrozenSet[str]:
    """Staff IDs allowed to list/manage all connectable devices (admin only)."""
    return _scope("device_admin")
