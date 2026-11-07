"""Ownership consumer ↔ Plugin conformance, using committed SQL facts."""

from __future__ import annotations

import pytest
from injector import Module, provider, singleton

from agentclaw.community.core.bot_management.ownership_sync.service import (
    BotOwnershipSyncService,
)
from agentclaw.community.core.repository.implementations.bot.ownership_sync import (
    SqlOwnershipSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure, SyncOutcome
from agentclaw.community.plugin_api.bcs_ownership import (
    BcsOwnershipError,
    BcsOwnershipPlugin,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.local.bcs_ownership import RecordingBcsOwnershipPlugin
from tests.community.core.bot_collaborator.manager_sync.factories import seed_sync_facts


class ConsumerModule(Module):
    @singleton
    @provider
    def plugin(self) -> BcsOwnershipPlugin:
        return RecordingBcsOwnershipPlugin()

    @singleton
    @provider
    def consumer(
        self, db: DatabasePlugin, bcs: BcsOwnershipPlugin
    ) -> BotOwnershipSyncService:
        return BotOwnershipSyncService(SqlOwnershipSyncReadRepository(db), bcs)


@pytest.fixture
def ownership_consumer(world):
    world.injector.binder.install(ConsumerModule())
    key = seed_sync_facts(
        world.get(DatabasePlugin),
        tenant="t",
        env="dev",
        bot_pk=7,
        entity_id="stable",
        owner_id="B",
        rows=(),
        space_id=None,
    )
    return key, world.get(BotOwnershipSyncService), world.get(BcsOwnershipPlugin)


@pytest.mark.asyncio
async def test_consumer_delivers_committed_owner_through_plugin(ownership_consumer):
    key, consumer, plugin = ownership_consumer
    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED
    assert len(plugin.calls) == 1
    assert plugin.calls[0].key == key
    assert plugin.calls[0].owner_user_id == "B"
    assert plugin.calls[0].bcs_bot_id == "bot-stable:stable"


@pytest.mark.asyncio
async def test_consumer_rejection_calls_plugin_once_and_allows_later_round(
    ownership_consumer,
):
    key, consumer, plugin = ownership_consumer
    plugin.outcomes.append(BcsOwnershipError(DeliveryFailure.REJECTED, "http_403"))
    assert await consumer.sync_once(key) is SyncOutcome.FAILED
    assert len(plugin.calls) == 1
    assert plugin.calls[0].key == key
    assert plugin.calls[0].owner_user_id == "B"
    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED
    assert len(plugin.calls) == 2
