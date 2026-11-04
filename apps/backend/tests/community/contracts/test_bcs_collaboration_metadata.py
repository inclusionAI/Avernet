"""Collaboration metadata consumer ↔ Plugin conformance over committed facts.

The real ``CollaborationRepairService`` reads committed SQL facts through
the real repository and sends through the recording Plugin implementations
installed by this module — the same shape Rule 25 asks for: an upper-layer
consumer driven against the local implementation as the executable spec.

The seeded Bot has never established a caller config (revision ``0``), so
``caller_config`` is ``not_applicable`` and the recorded sends are exactly
the metadata probe plus the profile update. Applicability is deliberate: a
suite that silently sent a caller item would weaken the call-sequence
assertion below.
"""

from __future__ import annotations

import time

import pytest
from injector import Module, provider, singleton

from agentclaw.community.core.bot_management.collaboration_repair.service import (
    CollaborationRepairService,
)
from agentclaw.community.core.repository.implementations.bot.collaboration_repair import (
    SqlCollaborationRepairReadRepository,
)
from agentclaw.community.core.repository.implementations.bot.manager_sync import (
    SqlManagerSyncReadRepository,
)
from agentclaw.community.core.repository.implementations.bot.ownership_sync import (
    SqlOwnershipSyncReadRepository,
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
from agentclaw.community.kernel.bot_manager_sync import SnapshotUnavailable, SyncKey
from agentclaw.community.kernel.collaboration_repair import (
    RepairCommand,
    RepairPreconditionError,
)
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugin_api.bcs_managers import BcsManagersPlugin
from agentclaw.community.plugin_api.bcs_ownership import BcsOwnershipPlugin
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.local.bcs_collaboration_metadata import (
    RecordingBcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin
from tests.community.core.bot_collaborator.manager_sync.factories import seed_sync_facts

_TENANT = "t"
_ENV = "dev"
_BOT_PK = 7
_BOT_ID = "b"
_ENTITY_ID = "e"
_STABLE = f"{_BOT_ID}:{_ENTITY_ID}"
_OWNER = "owner"
_TIMEOUT_SECONDS = 30.0


class ConsumerModule(Module):
    """Bind the real consumer, the real readers and the recording delivery."""

    def configure(self, binder) -> None:
        binder.bind(
            CollaborationRepairReadRepository,
            to=SqlCollaborationRepairReadRepository,
            scope=singleton,
        )
        binder.bind(
            OwnershipSyncReadRepository,
            to=SqlOwnershipSyncReadRepository,
            scope=singleton,
        )
        binder.bind(
            ManagerSyncReadRepository,
            to=SqlManagerSyncReadRepository,
            scope=singleton,
        )
        binder.bind(
            BcsOwnershipPlugin, to=RecordingBcsOwnershipPlugin, scope=singleton
        )
        binder.bind(BcsManagersPlugin, to=RecordingBcsManagersPlugin, scope=singleton)

    @singleton
    @provider
    def metadata(self) -> BcsCollaborationMetadataPlugin:
        # Existing-only: the seeded Bot is the only id this fake knows.
        return RecordingBcsCollaborationMetadataPlugin(existing=(_STABLE,))

    @singleton
    @provider
    def consumer(
        self,
        reader: CollaborationRepairReadRepository,
        ownership_reader: OwnershipSyncReadRepository,
        managers_reader: ManagerSyncReadRepository,
        metadata: BcsCollaborationMetadataPlugin,
        ownership: BcsOwnershipPlugin,
        managers: BcsManagersPlugin,
    ) -> CollaborationRepairService:
        return CollaborationRepairService(
            reader,
            ownership_reader,
            managers_reader,
            metadata,
            ownership,
            managers,
            timeout_seconds=_TIMEOUT_SECONDS,
            clock=time.monotonic,
        )


@pytest.fixture
def repair_world(world):
    world.injector.binder.install(ConsumerModule())
    seed_sync_facts(
        world.get(DatabasePlugin),
        tenant=_TENANT,
        env=_ENV,
        bot_pk=_BOT_PK,
        entity_id=_ENTITY_ID,
        owner_id=_OWNER,
        rows=(("member", "member"),),
        space_id=None,
        bot_id=_BOT_ID,
    )
    command = RepairCommand(SyncKey(_TENANT, _ENV, _BOT_PK), _OWNER)
    return (
        command,
        world.get(CollaborationRepairService),
        world.get(BcsCollaborationMetadataPlugin),
    )


@pytest.mark.asyncio
async def test_consumer_uses_metadata_plugin(repair_world):
    command, service, metadata = repair_world
    report = await service.repair(command)
    assert report.status == "succeeded"
    assert [name for name, value in metadata.calls] == ["exists", "profile"]


@pytest.mark.asyncio
async def test_missing_consumer_never_creates(repair_world):
    command, service, metadata = repair_world
    metadata.existing.clear()
    with pytest.raises(RepairPreconditionError, match="collaboration_bot_not_found"):
        await service.repair(command)
    assert [name for name, value in metadata.calls] == ["exists"]


@pytest.mark.asyncio
async def test_success_records_the_ownership_and_manager_writes(world, repair_world):
    """The same repair reaches every recording Plugin, addressed by the ref."""
    command, service, _metadata = repair_world
    report = await service.repair(command)
    ownership = world.get(BcsOwnershipPlugin)
    managers = world.get(BcsManagersPlugin)

    assert report.status == "succeeded"
    assert [item.component for item in report.items] == [
        "ownership",
        "profile",
        "managers",
        "caller_config",
    ]
    assert [call.bcs_bot_id for call in ownership.calls] == [_STABLE]
    assert [call.bcs_bot_id for call in managers.calls] == [_STABLE]
    assert managers.calls[0].manager_user_ids == ("member",)


@pytest.mark.asyncio
async def test_manager_facts_failure_keeps_confirmed_plugin_outcomes(
    world, repair_world, monkeypatch
):
    command, service, metadata = repair_world

    def unavailable(key):
        raise SnapshotUnavailable("read_error")

    monkeypatch.setattr(world.get(ManagerSyncReadRepository), "read_facts", unavailable)
    report = await service.repair(command)
    assert report.status == "partial_failed"
    assert [item.status for item in report.items] == [
        "succeeded", "succeeded", "failed", "not_applicable"
    ]
    assert report.items[2].error_code == "collaboration_facts_read_error"
    assert [name for name, value in metadata.calls] == ["exists", "profile"]
    assert len(world.get(BcsOwnershipPlugin).calls) == 1
    assert world.get(BcsManagersPlugin).calls == []
