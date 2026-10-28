"""Conformance suite for BcsManagersPlugin (Rule 25).

The executable contract is the one the *consumer* sees: this suite drives
the real ``BotManagerSyncService`` resolved from the test-profile ``world``
fixture — SQLite reader + recording BCS + memory coordinator — and pins
that every ``replace_managers`` verdict the Protocol defines surfaces at
the consumer as the kernel's ``SyncOutcome`` mapping:

- one confirmed full replace (the snapshot's exact manager list, owner
  excluded, deduped + sorted) is ``CONFIRMED`` through the plugin,
- ``DeliveryFailure.UNKNOWN`` keeps the in-flight marker: a second
  consumer round is ``BLOCKED`` and the plugin recorded exactly one
  delivery attempt — never a replay,
- ``DeliveryFailure.REJECTED`` is terminal for the round (``FAILED``),
  and a confirmed ``total`` that disagrees with the pushed roster is
  ``DRIFT``, never ``CONFIRMED``,
- an empty roster is faithfully delivered as ``[]`` and acknowledged as
  ``ACKNOWLEDGED_EMPTY``, never padded or dropped.

Nothing here calls the plugin fake directly as evidence — every verdict
is produced by ``consumer.sync_once`` and read back from the recording
plugin's ``calls`` (the shared-DI singleton the service actually uses).
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

# The recording BCS + memory coordination doubles are bound by the
# test/singlebox columns; other profiles bind different implementations
# with their own suites.
_allow_profiles = {"test", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"BcsManagersPlugin contract only valid under {_allow_profiles}, "
    f"got DEPLOY_PROFILE={_current_profile!r}",
)


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
async def test_consumer_unknown_delivery_blocks_second_round(world):
    """One UNKNOWN write pins the key: no replay while the marker stands.

    ``DeliveryFailure.UNKNOWN`` means the plugin could not answer whether
    BCS applied the replace — the consumer must keep the in-flight
    marker, report ``UNKNOWN``, and a second consumer round must be
    ``BLOCKED`` at claim time with the plugin still holding exactly one
    recorded delivery.
    """
    db = world.get(DatabasePlugin)
    key = seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=8,
        entity_id="entity",
        owner_id="owner",
        rows=(("member", "member"),),
        space_id=None,
    )
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)
    plugin.outcomes.append(BcsManagersError(DeliveryFailure.UNKNOWN, "read_timeout"))

    assert await consumer.sync_once(key) is SyncOutcome.UNKNOWN
    # The possibly-written marker survives: the second round is blocked...
    assert await consumer.sync_once(key) is SyncOutcome.BLOCKED
    # ...and the uncertain delivery was never replayed or superseded.
    assert len(plugin.calls) == 1
    assert plugin.calls[-1].key == key


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
    # The attempt was finished (marker cleared): the key is claimable
    # again and a clean retry may send — with a fully scripted history.
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
