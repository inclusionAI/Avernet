"""Conformance suite for ManagerSyncCoordinator (Rule 25).

The strict coordination boundary exists for the *consumer*: this suite
drives the real ``BotManagerSyncService`` resolved from the test-profile
``world`` fixture (SQLite reader + recording BCS + memory coordinator)
and pins the lease/marker semantics from the consumer's verdicts, never
by exercising the coordinator fake in isolation:

- **lease** — a second round while one consumer holds the lease (parked
  before ``begin``) answers ``DEFERRED`` (claim BUSY), never a send;
- **begin / in-flight marker** — once an attempt is open (parked inside
  the PUT), a second round is ``BLOCKED`` even though the lease is
  live: the TTL-less marker, not the lease, carries the safety;
- **finish (CAS)** — a confirmed round frees exactly its own attempt:
  the immediately following round claims and ``CONFIRM``s again, and a
  changed roster reaches BCS with the new digest;
- **owner-hold marker** — ``hold_owner_check`` gates the same key
  against roster sends (claim sees the hold), with no delivery involved,
  and coexists with an in-flight marker left by an uncertain delivery;
- **unknown marker** — an in-flight record left by an uncertain delivery
  keeps blocking later rounds while the plugin stops being called;
- **release hygiene** — finished rounds leave no residue: a burst of
  sequential rounds on one key never degenerates into permanent
  BUSY/BLOCKED state.

Hang points are Event/barrier-gated (the reader parks on a threading
``Event`` inside its ``to_thread`` call, the plugin on an asyncio
``Event`` inside the PUT) — no millisecond thresholds anywhere.
"""

from __future__ import annotations

import asyncio
import os
import threading

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.repository.protocols.bot.manager_sync import (
    ManagerSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    SyncOutcome,
)
from agentclaw.community.plugin_api.bcs_managers import (
    BcsManagersError,
    BcsManagersPlugin,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from tests.community.core.bot_collaborator.manager_sync.factories import (
    seed_sync_facts,
)

# Same profile rule as the bcs_managers suite: the memory coordinator is
# bound by the test/singlebox columns.
_allow_profiles = {"test", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"ManagerSyncCoordinator contract only valid under {_allow_profiles}, "
    f"got DEPLOY_PROFILE={_current_profile!r}",
)


def _seed_key(world, bot_pk: int):
    db = world.get(DatabasePlugin)
    return seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=bot_pk,
        entity_id=f"entity-{bot_pk}",
        owner_id="owner",
        rows=(("member", "member"),),
        space_id=None,
    )


def _add_collaborator_row(world, key, user_id: str, role: str = "member") -> None:
    """One more explicit collaborator row on an already-seeded Bot."""
    db = world.get(DatabasePlugin)
    bot = world.get(ManagerSyncReadRepository).read_facts(key)
    with avernet_tenant_scope(key.avernet_tenant), db.orm_session() as session:
        session.add(
            BotCollaboratorModel(
                bot_pk=key.bot_pk,
                bot_id=bot.bot_id,
                owner_id=bot.owner_id,
                user_id=user_id,
                user_name=user_id,
                role=role,
                operator_id=bot.owner_id,
                env=key.env,
            )
        )


@pytest.mark.asyncio
async def test_consumer_defers_second_round_while_lease_is_held(world, monkeypatch):
    """Another holder's live lease is the only DEFERRED — no send, no block.

    The first round is parked inside its threaded facts read (past the
    claim, before ``begin``): the lease is provably held and no marker
    exists, which is exactly the claim-BUSY window.
    """
    key = _seed_key(world, 21)
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)
    reader = world.get(ManagerSyncReadRepository)

    entered = threading.Event()
    release = threading.Event()
    real_read_facts = reader.read_facts

    def _hanging_read(k):
        entered.set()
        release.wait(timeout=10)
        return real_read_facts(k)

    monkeypatch.setattr(reader, "read_facts", _hanging_read)

    round_one = asyncio.create_task(consumer.sync_once(key))
    await asyncio.to_thread(entered.wait, 10)

    # Claim BUSY while the other holder is mid-round, pre-marker.
    assert await consumer.sync_once(key) is SyncOutcome.DEFERRED
    assert plugin.calls == []  # a deferred round sends nothing

    release.set()
    assert await round_one is SyncOutcome.CONFIRMED


@pytest.mark.asyncio
async def test_consumer_blocks_second_round_while_attempt_is_inflight(
    world, monkeypatch
):
    """An open in-flight marker blocks even a lease-eligible second round.

    The first round is parked inside the PUT (post-``begin``): the
    marker — not the lease — is what a second consumer round sees.
    """
    key = _seed_key(world, 22)
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)

    entered = asyncio.Event()
    release = asyncio.Event()
    real_replace = plugin.replace_managers

    async def _hanging_replace(snapshot, *, timeout_seconds):
        entered.set()
        await release.wait()
        return await real_replace(snapshot, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(plugin, "replace_managers", _hanging_replace)

    round_one = asyncio.create_task(consumer.sync_once(key))
    await entered.wait()

    # The PUT was invoked and the attempt is open: BLOCKED, not DEFERRED.
    assert await consumer.sync_once(key) is SyncOutcome.BLOCKED
    assert plugin.calls == []  # the blocked round reached no delivery

    release.set()
    assert await round_one is SyncOutcome.CONFIRMED
    # Total deliveries for the whole episode: exactly the one round that
    # owned the open attempt — the blocked round never sent.
    assert len(plugin.calls) == 1


@pytest.mark.asyncio
async def test_consumer_finish_clears_exactly_its_attempt_and_reconfirms(world):
    """A confirmed round frees the key: the next round re-delivers cleanly.

    Finish is a compare-and-delete of exactly this round's attempt; the
    immediately following round claims a clean boundary, and a roster
    change in between reaches BCS as a NEW digest —  never a replay of
    the first snapshot.
    """
    key = _seed_key(world, 23)
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)

    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED
    _add_collaborator_row(world, key, "second-member", role="admin")

    assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED

    assert len(plugin.calls) == 2
    assert plugin.calls[0].key == key and plugin.calls[1].key == key
    assert plugin.calls[0].digest != plugin.calls[1].digest
    assert plugin.calls[0].manager_user_ids == ("member",)
    assert plugin.calls[1].manager_user_ids == ("member", "second-member")


@pytest.mark.asyncio
async def test_consumer_owner_hold_gates_roster_sends_without_delivery(world):
    """``hold_owner_check`` plants the independent owner-gate marker.

    The hold plants its marker with zero plugin involvement (no roster
    read, no PUT), and a roster round on the held key is BLOCKED at
    claim time. Re-holding is idempotent — the same marker answers.
    """
    key = _seed_key(world, 24)
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)

    assert await consumer.hold_owner_check(key) is SyncOutcome.BLOCKED
    assert await consumer.sync_once(key) is SyncOutcome.BLOCKED
    # Idempotent: the second hold sees the same owner-gate state.
    assert await consumer.hold_owner_check(key) is SyncOutcome.BLOCKED
    # No roster send ever ran for the held key.
    assert plugin.calls == []


@pytest.mark.asyncio
async def test_consumer_unknown_marker_coexists_with_owner_hold(world):
    """An unknown-delivery marker survives beside the owner-hold marker.

    After an uncertain delivery the in-flight marker blocks roster
    rounds; planting the owner hold on the same coexisting marker
    still answers BLOCKED, and the plugin recorded exactly the one
    uncertain delivery.
    """
    key = _seed_key(world, 25)
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)
    plugin.outcomes.append(BcsManagersError(DeliveryFailure.UNKNOWN, "read_timeout"))

    assert await consumer.sync_once(key) is SyncOutcome.UNKNOWN
    assert await consumer.hold_owner_check(key) is SyncOutcome.BLOCKED
    assert await consumer.sync_once(key) is SyncOutcome.BLOCKED
    assert len(plugin.calls) == 1


@pytest.mark.asyncio
async def test_consumer_burst_rounds_leave_no_coordination_residue(world):
    """Sequential confirmed rounds release both lease and marker each time."""
    key = _seed_key(world, 26)
    consumer = world.get(BotManagerSyncService)
    plugin = world.get(BcsManagersPlugin)

    for round_no in range(5):
        assert await consumer.sync_once(key) is SyncOutcome.CONFIRMED, round_no

    assert len(plugin.calls) == 5
