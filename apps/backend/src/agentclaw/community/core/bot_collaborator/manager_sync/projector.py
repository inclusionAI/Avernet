"""The manager set projector — ``SyncFacts`` in, ``ManagerSnapshot`` out.

A pure function over in-memory facts: no database, no plugins, no
logging of the projected roster (the id list labels travel inside the
payload/digest, but nothing here writes them anywhere).

Rules (fixed by the Task 1 type dictionary):

- identity: ``bcs_bot_id`` is ``"<bot_id>:<entity_id>"`` — the stable
  identity that survives an owner handoff. Missing identity raises
  ``SnapshotUnavailable``; an unknown collaborator role or an empty
  ``user_id`` is a hard ``ValueError`` — never silently projected as a
  member.
- membership: ``admin`` / ``member`` rows that are still
  ``space_eligible``, excluding the current owner, deduped and sorted.
- cap: at most 1,000 manager ids — ``SnapshotTooLarge`` beyond that.
- digest: SHA-256 over canonical JSON of key, target, owner, and the
  full id list, so it tracks the desired state rather than input order.
"""
from __future__ import annotations

import hashlib
import json

from agentclaw.community.kernel.bot_manager_sync import (
    ManagerSnapshot,
    SnapshotTooLarge,
    SnapshotUnavailable,
    SyncFacts,
    SyncKey,
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

    user_ids = tuple(sorted({row.user_id for row in facts.collaborators
                             if row.role in MANAGER_ROLES
                             and row.space_eligible
                             and row.user_id != facts.owner_id}))
    if len(user_ids) > MAX_MANAGER_USER_IDS:
        raise SnapshotTooLarge()

    bcs_bot_id = f"{facts.bot_id}:{facts.entity_id}"
    return ManagerSnapshot(
        key=facts.key,
        bcs_bot_id=bcs_bot_id,
        owner_id=facts.owner_id,
        manager_user_ids=user_ids,
        digest=_digest(facts.key, bcs_bot_id, facts.owner_id, user_ids),
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


def _digest(
    key: SyncKey, bcs_bot_id: str, owner_id: str, user_ids: tuple[str, ...]
) -> str:
    payload = json.dumps(
        {
            "avernet_tenant": key.avernet_tenant,
            "env": key.env,
            "bot_pk": key.bot_pk,
            "bcs_bot_id": bcs_bot_id,
            "owner_id": owner_id,
            "manager_user_ids": list(user_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()