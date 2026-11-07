"""Consumer conformance for authoritative, best-effort full-roster delivery.

World resolves the real consumer and a recording BCS plugin. Transport uncertainty
is retryable within the bounded round; no coordinator or permanent block exists.
"""
from __future__ import annotations

import os

import pytest

from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    ManagerSetChange,
    SyncOutcome,
)
from agentclaw.community.plugin_api.bcs_managers import (
    BcsManagersError,
    BcsManagersPlugin,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.core.bot_collaborator.manager_sync.factories import (
    seed_sync_facts,
)

# Recording delivery is installed explicitly for consumer conformance.
_allow_profiles = {"test", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"BcsManagersPlugin contract only valid under {_allow_profiles}, "
    f"got DEPLOY_PROFILE={_current_profile!r}",
)


@pytest.fixture(autouse=True)
def recording_delivery(world):
    from agentclaw.community.di.modules.infrastructure.test.bot_manager_sync import TestBotManagerSyncModule

    world.injector.binder.install(TestBotManagerSyncModule())



@pytest.mark.asyncio
async def test_consumer_replaces_managers_through_plugin(world):
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=7,
        entity_id="entity",
        owner_id="owner",
        rows=(("member", "member"),),
        space_id=None,
    )
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)
    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED
    assert plugin.calls[-1].key == key
    assert plugin.calls[-1].manager_user_ids == ("member",)


@pytest.mark.asyncio
async def test_consumer_unknown_retries_then_allows_a_later_round(world):
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(db, tenant="a", env="dev", bot_pk=8,
        entity_id="entity", owner_id="owner", rows=(("member", "member"),), space_id=None)
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)
    plugin.outcomes.extend([
        BcsManagersError(DeliveryFailure.UNKNOWN, "read_timeout") for _ in range(3)
    ])
    assert await consumer.sync_once(key) is SyncOutcome.FAILED
    assert len(plugin.calls) == 3
    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED
    assert len(plugin.calls) == 4
    assert all(call.key == key for call in plugin.calls)


@pytest.mark.asyncio
async def test_consumer_rejected_delivery_is_terminal_failed(world):
    """A considered-and-refused PUT ends the round FAILED — no retry."""
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=9,
        entity_id="entity",
        owner_id="owner",
        rows=(("member", "member"),),
        space_id=None,
    )
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)
    plugin.outcomes.append(BcsManagersError(DeliveryFailure.REJECTED, "http_403"))

    assert await consumer.sync_once(key) is SyncOutcome.FAILED
    # A failed round does not prevent a later independent synchronization.
    plugin.outcomes.clear()
    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED
    assert len(plugin.calls) == 2


@pytest.mark.asyncio
async def test_consumer_reports_drift_when_bcs_applies_another_set(world):
    """A confirmed total that disagrees with the roster is DRIFT.

    The plugin's ``ManagerSetChange`` is BCS's *report of what it now
    holds*; if that answer cannot be the projection's own roster, the
    consumer must surface the mismatch instead of trusting the "success".
    """
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=10,
        entity_id="entity",
        owner_id="owner",
        rows=(("member", "member"),),
        space_id=None,
    )
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)
    plugin.outcomes.append(
        ManagerSetChange(added=0, removed=0, total=99, request_id="bcs-drift")
    )

    assert await consumer.sync_once(key) is SyncOutcome.DRIFT
    assert plugin.calls[-1].manager_user_ids == ("member",)


@pytest.mark.asyncio
async def test_consumer_delivers_empty_roster_and_acknowledges_empty(world):
    """An empty manager list is faithfully PUT as ``[]`` — never skipped."""
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=11,
        entity_id="entity",
        owner_id="owner",
        rows=(),
        space_id=None,
    )
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)

    assert await consumer.sync_once(key) is SyncOutcome.ACKNOWLEDGED_EMPTY
    assert plugin.calls[-1].manager_user_ids == ()
