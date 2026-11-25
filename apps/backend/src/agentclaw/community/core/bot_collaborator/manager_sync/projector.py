"""The manager set projector — ``SyncFacts`` in, ``ManagerSnapshot`` out.

A pure function over in-memory facts: no database, no plugins, no
logging of the projected roster (the id list travels only inside the
returned payload; nothing here writes it anywhere).

Rules (fixed by the Task 1 type dictionary):

- identity: use the established TeamClaw registration reference, retaining
  conversion addresses and personal/default legacy identities. Missing identity raises
  ``SnapshotUnavailable``; an unknown collaborator role or an empty
  ``user_id`` is a hard ``ValueError`` — never silently projected as a
  member.
- membership: ``admin`` / ``member`` rows that are still
  ``space_eligible``, excluding the current owner, deduped and sorted.
- cap: at most 1,000 manager ids — ``SnapshotTooLarge`` beyond that.
"""

from __future__ import annotations

from agentclaw.community.core.bot_management.bcn_identity import bcn_bot_reference
from agentclaw.community.kernel.bot_manager_sync import (
    ManagerSnapshot,
    SnapshotTooLarge,
    SnapshotUnavailable,
    SyncFacts,
)

#: The only roles that make someone a BCS manager; anything read from
#: persistence outside this set is bad data, not a quiet "member".
MANAGER_ROLES = frozenset({"admin", "member"})

#: Hard cap on one manager projection — a Bot with a bigger projected
#: roster is bad data and must be surfaced (SnapshotTooLarge), not
#: silently truncated.
MAX_MANAGER_USER_IDS = 1000


def build_snapshot(facts: SyncFacts) -> ManagerSnapshot:
    """Project one Bot's collaborator facts onto its BCS manager set."""
    _require_identity(facts)
    for row in facts.collaborators:
        if row.role not in MANAGER_ROLES:
            raise ValueError(
                f"unknown collaborator role {row.role!r} for "
                f"bot_pk={facts.key.bot_pk}; refusing to project as member"
            )
        if not row.user_id:
            raise ValueError(
                f"collaborator with empty user_id for bot_pk={facts.key.bot_pk}"
            )

    user_ids = tuple(
        sorted(
            {
                row.user_id
                for row in facts.collaborators
                if row.role in MANAGER_ROLES
                and row.space_eligible
                and row.user_id != facts.owner_id
            }
        )
    )
    if len(user_ids) > MAX_MANAGER_USER_IDS:
        raise SnapshotTooLarge()

    if facts.bot_type is not None and not isinstance(facts.bot_type, str):
        raise SnapshotUnavailable("invalid_bot_type")
    try:
        bcs_bot_id = bcn_bot_reference(
            {
                "bot_id": facts.bot_id,
                "entity_id": facts.entity_id,
                "owner_id": facts.owner_id,
                "bot_type": facts.bot_type,
                "ext": {"bcn_provider_bot_ref": facts.bcn_provider_bot_ref},
            }
        )
    except ValueError as exc:
        raise SnapshotUnavailable("invalid_bcn_identity") from exc
    return ManagerSnapshot(
        key=facts.key,
        bcs_bot_id=bcs_bot_id,
        owner_id=facts.owner_id,
        manager_user_ids=user_ids,
    )


def _require_identity(facts: SyncFacts) -> None:
    """A Bot without a full stable identity cannot be addressed in BCS."""
    if not facts.key.avernet_tenant:
        raise SnapshotUnavailable("missing_tenant")
    if not facts.key.env:
        raise SnapshotUnavailable("missing_env")
    if not facts.bot_id:
        raise SnapshotUnavailable("missing_bot_id")
    if not facts.entity_id:
        raise SnapshotUnavailable("missing_entity_id")
    if not facts.owner_id:
        raise SnapshotUnavailable("missing_owner_id")
