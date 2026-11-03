"""Unit tests for the manager sync set projector.

The projector is a pure function over ``SyncFacts``: no database, no
plugins, no transport. Every case builds facts inline and asserts the
projected ``ManagerSnapshot``.

Projection rules pinned here (Task 1 type dictionary):

- identity: ``bcs_bot_id`` is ``"<bot_id>:<entity_id>"`` and survives an
  owner change;
- membership: exactly the ``admin`` / ``member`` collaborators that are
  still ``space_eligible``, excluding the owner, deduped and sorted;
- rejection: unknown roles and empty ``user_id`` rows raise instead of
  being silently projected as members;
- cap: at most 1,000 manager ids; one more raises ``SnapshotTooLarge``.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
    build_snapshot,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    SnapshotTooLarge,
    SnapshotUnavailable,
    SyncFacts,
    SyncKey,
)


def _facts(
    collaborators: tuple[CollaboratorFact, ...],
    *,
    owner_id: str = "new-owner",
    entity_id: str = "stable-entity",
) -> SyncFacts:
    return SyncFacts(
        SyncKey("tenant-a", "dev", 7),
        "default",
        entity_id,
        owner_id,
        collaborators,
    )


def test_all_explicit_roles_but_not_owner_or_revoked_member():
    facts = SyncFacts(
        SyncKey("tenant-a", "dev", 7),
        "default",
        "stable-entity",
        "new-owner",
        (
            CollaboratorFact("admin", "admin", True),
            CollaboratorFact("member", "member", True),
            CollaboratorFact("new-owner", "admin", True),
            CollaboratorFact("revoked", "member", False),
        ),
    )
    result = build_snapshot(facts)
    assert result.bcs_bot_id == "default:stable-entity"
    assert result.manager_user_ids == ("admin", "member")


def test_empty_collaborators_project_empty_manager_set():
    result = build_snapshot(_facts(()))
    assert result.manager_user_ids == ()
    assert result.bcs_bot_id == "default:stable-entity"


def test_duplicate_rows_dedupe_and_sort():
    facts = _facts(
        (
            CollaboratorFact("zeta", "member", True),
            CollaboratorFact("alpha", "admin", True),
            CollaboratorFact("alpha", "member", True),
            CollaboratorFact("zeta", "member", True),
        ),
    )
    result = build_snapshot(facts)
    assert result.manager_user_ids == ("alpha", "zeta")


def test_role_change_between_admin_and_member_keeps_ids():
    admin_first = build_snapshot(
        _facts(
            (
                CollaboratorFact("u1", "admin", True),
                CollaboratorFact("u2", "member", True),
            )
        )
    )
    roles_swapped = build_snapshot(
        _facts(
            (
                CollaboratorFact("u1", "member", True),
                CollaboratorFact("u2", "admin", True),
            )
        )
    )
    assert admin_first.manager_user_ids == roles_swapped.manager_user_ids


def test_unknown_role_rejected_not_silently_projected_as_member():
    facts = _facts(
        (
            CollaboratorFact("admin", "admin", True),
            CollaboratorFact("guest", "guest", True),
        ),
    )
    with pytest.raises(ValueError, match="role"):
        build_snapshot(facts)


def test_empty_user_id_rejected():
    facts = _facts((CollaboratorFact("", "member", True),))
    with pytest.raises(ValueError, match="user_id"):
        build_snapshot(facts)


def test_cap_allows_1000_managers_and_rejects_1001():
    thousand = tuple(
        CollaboratorFact(f"user-{i:04d}", "member", True) for i in range(1000)
    )
    assert len(build_snapshot(_facts(thousand)).manager_user_ids) == 1000
    with pytest.raises(SnapshotTooLarge):
        build_snapshot(_facts(thousand + (CollaboratorFact("extra", "member", True),)))


def test_owner_change_keeps_bcs_identity_and_excludes_new_owner():
    old_owner = build_snapshot(
        _facts(
            (
                CollaboratorFact("admin", "admin", True),
                CollaboratorFact("old-owner", "admin", True),
            ),
            owner_id="old-owner",
        )
    )
    new_owner = build_snapshot(
        _facts(
            (
                CollaboratorFact("admin", "admin", True),
                CollaboratorFact("new-owner", "admin", True),
            ),
            owner_id="new-owner",
        )
    )
    assert old_owner.bcs_bot_id == new_owner.bcs_bot_id == "default:stable-entity"
    assert old_owner.manager_user_ids == ("admin",)
    assert new_owner.manager_user_ids == ("admin",)


def test_missing_stable_identity_raises_snapshot_unavailable():
    with pytest.raises(SnapshotUnavailable):
        build_snapshot(_facts((), entity_id=""))
    with pytest.raises(SnapshotUnavailable):
        build_snapshot(_facts((), owner_id=""))
