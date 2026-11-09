"""Factory for the one-shot repair service unit cases.

``make_repair_case`` builds everything the service tests need through the
PUBLIC constructor only — Mock readers (typed by their read protocols),
AsyncMock delivery plugins (typed by their Plugin API Protocols), the Task 4
kernel dataclasses, and a controllable ``clock``. Order assertions use a
shared attach-parent so ``manager.mock_calls`` records every reader and
delivery interaction in real call order; per-item outcomes are scripted via
plain ``return_value`` / ``side_effect`` so the plan's RED test can flip
``metadata.exists.return_value`` without fighting a default side effect.

The fixture facts are pinned by the plan: key ``tenant-a/dev/7``, stable
address ``b:e``, current owner ``owner-new``. Every OTHER default is derived
from the effective initial facts, so a ``facts=`` / ``manager_facts=`` /
``ownership_facts=`` override can never leave the case internally
inconsistent. The default managers roster is deliberately mixed — the owner
as a collaborator, a Space-ineligible row, an unsorted admin pair with a
duplicate — and the expected roster is computed by the REAL projector, so
its owner-exclusion / Space / sort / dedupe rules are observable through the
service without re-encoding them here.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

from agentclaw.community.core.bot_collaborator.manager_sync.projector import (
    build_snapshot as build_manager_snapshot,
)
from agentclaw.community.core.repository.protocols.bot.collaboration_repair import (
    CollaborationRepairReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.core.repository.protocols.bot.ownership_sync import (
    OwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    ManagerSetChange,
    SyncFacts,
    SyncKey,
)
from agentclaw.community.kernel.bot_ownership_sync import (
    OwnershipChange,
    OwnershipFacts,
)
from agentclaw.community.kernel.collaboration_repair import (
    ProfileSnapshot,
    RepairCommand,
    RepairFacts,
)
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipPlugin

#: The plan's pinned address facts.
KEY = SyncKey("tenant-a", "dev", 7)
STABLE = "b:e"
CURRENT_OWNER = "owner-new"

class Clock:
    """Deterministic monotonic clock; tests move ``now`` to trip deadlines."""

    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def default_profile() -> ProfileSnapshot:
    return ProfileSnapshot(
        bot_id="b",
        bcs_bot_id=STABLE,
        name="Bot B",
        summary="b summary",
        avatar_url="",
    )


def default_repair_facts(
    *,
    key: SyncKey = KEY,
    owner_user_id: str = CURRENT_OWNER,
) -> RepairFacts:
    """The one-pass repair facts exactly as Task 4's reader projects them."""
    return RepairFacts(
        key=key,
        profile=default_profile(),
        owner_user_id=owner_user_id,
    )


def default_manager_facts(
    *,
    key: SyncKey = KEY,
    bot_id: str = "b",
    entity_id: str = "e",
    owner_id: str = CURRENT_OWNER,
) -> SyncFacts:
    """Mixed collaborator rows for the effective identity/owner.

    The owner appears as a collaborator row (must be excluded), one row is
    Space-ineligible (must be excluded), and the eligible rows arrive
    unsorted with a duplicate — so the real projector's owner-exclusion,
    Space, sort and dedupe rules are all observable through the service.
    """
    return SyncFacts(
        key,
        bot_id,
        entity_id,
        owner_id,
        (
            CollaboratorFact("collab-2", "admin", True),
            CollaboratorFact("collab-1", "member", True),
            CollaboratorFact(owner_id, "member", True),
            CollaboratorFact("ineligible", "member", False),
            CollaboratorFact("collab-1", "member", True),
        ),
        bot_type="service",
    )


def projected_roster(manager_facts: SyncFacts) -> tuple[str, ...]:
    """The roster the REAL projector derives from these facts."""
    return build_manager_snapshot(manager_facts).manager_user_ids


def split_stable(stable: str) -> tuple[str, str]:
    """Split a ``<bot_id>:<entity_id>`` address into its two parts."""
    bot_id, _, entity_id = stable.partition(":")
    return bot_id, entity_id


def make_repair_case(
    *,
    timeout_seconds: float = 30.0,
    clock: Clock | None = None,
    facts: RepairFacts | None = None,
    ownership_facts: OwnershipFacts | None = None,
    manager_facts: SyncFacts | None = None,
    actor_user_id: str | None = None,
) -> SimpleNamespace:
    """Build one fully wired repair case via the service constructor.

    Every default is derived from the EFFECTIVE initial facts, so an
    override can never leave the case internally inconsistent: the
    ownership facts, the manager facts, the ownership confirmation and
    the expected manager roster all describe the same Bot, key and owner
    the reader actually returns.

    ``actor_user_id`` defaults to the effective facts owner (the
    authorized case). Overrides arrive as plain mock attributes after the
    case is built (``case.managers.replace_managers.side_effect = ...``);
    every default is a ``return_value``, never a ``side_effect``, so
    scripted exceptions and return flips never fight a default script.
    """
    from agentclaw.community.core.bot_management.collaboration_repair.service import (
        CollaborationRepairService,
    )

    clock = clock if clock is not None else Clock()
    facts = facts if facts is not None else default_repair_facts()
    key = facts.key
    stable = facts.profile.bcs_bot_id
    effective_owner = facts.owner_user_id
    if actor_user_id is None:
        actor_user_id = effective_owner

    effective_manager_facts = manager_facts
    if effective_manager_facts is None:
        default_bot_id, default_entity_id = split_stable(stable)
        effective_manager_facts = default_manager_facts(
            key=key,
            bot_id=default_bot_id,
            entity_id=default_entity_id,
            owner_id=effective_owner,
        )
    if ownership_facts is None:
        bot_id, entity_id = split_stable(stable)
        ownership_facts = OwnershipFacts(
            key,
            bot_id,
            entity_id,
            effective_owner,
            bot_type="service",
        )

    reader = Mock(spec=CollaborationRepairReadRepository)
    reader.read_facts.return_value = facts
    ownership_reader = Mock(spec=OwnershipSyncReadRepository)
    ownership_reader.read_facts.return_value = ownership_facts
    managers_reader = Mock(spec=ManagerSyncReadRepository)
    managers_reader.read_facts.return_value = effective_manager_facts

    metadata = AsyncMock(spec=BcsCollaborationMetadataPlugin)
    metadata.exists.return_value = True
    ownership = AsyncMock(spec=BcsOwnershipPlugin)
    ownership.replace_ownership.return_value = OwnershipChange(
        stable,
        ownership_facts.owner_id,
        "owner-old",
        True,
        "req-ownership-1",
    )
    managers = AsyncMock(spec=BcsManagersPlugin)
    roster = projected_roster(effective_manager_facts)
    managers.replace_managers.return_value = ManagerSetChange(
        added=len(roster), removed=0, total=len(roster), request_id="req-managers-1"
    )

    service = CollaborationRepairService(
        reader,
        ownership_reader,
        managers_reader,
        metadata,
        ownership,
        managers,
        timeout_seconds=timeout_seconds,
        clock=clock,
    )

    # One attach-parent so ``manager.mock_calls`` records every reader and
    # delivery interaction in real call order for the order assertions.
    parent: Any = Mock(name="repair_case_calls")
    parent.attach_mock(reader, "reader")
    parent.attach_mock(ownership_reader, "ownership_reader")
    parent.attach_mock(managers_reader, "managers_reader")
    parent.attach_mock(metadata, "metadata")
    parent.attach_mock(ownership, "ownership")
    parent.attach_mock(managers, "managers")

    return SimpleNamespace(
        service=service,
        command=RepairCommand(key, actor_user_id),
        key=key,
        stable=stable,
        facts=facts,
        ownership_facts=ownership_facts,
        manager_facts=effective_manager_facts,
        expected_roster=roster,
        reader=reader,
        ownership_reader=ownership_reader,
        managers_reader=managers_reader,
        metadata=metadata,
        ownership=ownership,
        managers=managers,
        calls=parent,
        clock=clock,
        timeout_seconds=timeout_seconds,
    )
