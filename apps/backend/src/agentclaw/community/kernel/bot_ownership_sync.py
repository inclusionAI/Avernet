"""Transport-free v1 values for Backend→BCS best-effort ownership sync.

Ownership facts are current Backend state; snapshots carry the stable Bot
address separately from its mutable Owner. These values perform no I/O and
introduce no persisted delivery state.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.kernel.bot_manager_sync import SyncKey


@dataclass(frozen=True)
class OwnershipFacts:
    """Current identity and Owner facts for one tenant/environment/Bot key."""

    key: SyncKey
    bot_id: str
    entity_id: str
    owner_id: str
    bot_type: str | None  # NULL is the existing legacy non-service registration.
    bcn_provider_bot_ref: str | None = None


@dataclass(frozen=True)
class OwnershipSnapshot:
    """Desired Owner for a stable BCS Bot address; Owner is always required."""

    key: SyncKey
    bcs_bot_id: str
    owner_user_id: str


@dataclass(frozen=True)
class OwnershipChange:
    """BCS confirmation; nullable Owners reflect the external no-op contract."""

    bot_id: str
    owner_user_id: str | None
    previous_owner_user_id: str | None
    changed: bool
    request_id: str
