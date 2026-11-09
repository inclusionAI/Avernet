"""Collaboration metadata consumer ↔ Plugin conformance over committed facts.

The real ``CollaborationRepairService`` reads committed SQL facts through
the real repository and sends through the recording Plugin implementations
installed by this module — the same shape Rule 25 asks for: an upper-layer
consumer driven against the local implementation as the executable spec.

Only presence probes and profile writes belong to this metadata port, even
for Bots with established caller configuration. Caller identity-mode updates
retain their separate normal synchronization path.
"""

from __future__ import annotations

import json
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
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local.bcs_collaboration_metadata import (
    RecordingBcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
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
    with avernet_tenant_scope(_TENANT), world.get(DatabasePlugin).orm_session() as session:
        session.query(BotModel).filter(BotModel.id == _BOT_PK).update(
            {"bot_desc": "Saved summary"}
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
@pytest.mark.parametrize(
    "bot_type,bot_id,reference,expected",
    [
        ("personal", "b", None, "b:owner"),
        ("desktop", "b", None, "b:owner"),
        ("service", "default", None, "default:owner"),
        ("service", "b", None, "b:e"),
        ("service", "b", "b:original-owner", "b:original-owner"),
    ],
)
async def test_all_components_use_the_established_registration_reference(
    world, repair_world, bot_type, bot_id, reference, expected,
):
    command, service, metadata = repair_world
    with avernet_tenant_scope(_TENANT), world.get(DatabasePlugin).orm_session() as session:
        session.query(BotModel).filter(BotModel.id == _BOT_PK).update({
            "bot_id": bot_id,
            "bot_type": bot_type,
            "ext": json.dumps({"bcn_provider_bot_ref": reference}),
            "call_type": "caller",
            "caller_config_revision": 9,
        })
    metadata.existing.clear()
    metadata.existing.add(expected)

    report = await service.repair(command)

    assert report.status == "succeeded"
    assert metadata.calls[0] == ("exists", expected)
    assert [operation for operation, _ in metadata.calls] == ["exists", "profile"]
    assert all(snapshot.bcs_bot_id == expected for _, snapshot in metadata.calls[1:])
    assert [snapshot.bcs_bot_id for snapshot in world.get(BcsOwnershipPlugin).calls] == [expected]
    assert [snapshot.bcs_bot_id for snapshot in world.get(BcsManagersPlugin).calls] == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("summary", ["", " \t\n"])
async def test_consumer_accepts_blank_summary_without_losing_other_items(
    world, repair_world, summary,
):
    command, service, metadata = repair_world
    await service.repair(command)
    metadata.calls.clear()
    with avernet_tenant_scope(_TENANT), world.get(DatabasePlugin).orm_session() as session:
        session.query(BotModel).filter(BotModel.id == _BOT_PK).update({"bot_desc": summary})

    report = await service.repair(command)

    assert report.status == "succeeded"
    assert report.items[1].status == "succeeded"
    assert report.items[1].error_code is None
    assert report.items[2].status == "succeeded"
    assert metadata.profiles[_STABLE].summary == "Saved summary"
    assert [operation for operation, _ in metadata.calls] == ["exists", "profile"]


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
        "succeeded", "succeeded", "failed"
    ]
    assert report.items[2].error_code == "collaboration_facts_read_error"
    assert [name for name, value in metadata.calls] == ["exists", "profile"]
    assert len(world.get(BcsOwnershipPlugin).calls) == 1
    assert world.get(BcsManagersPlugin).calls == []
