"""Resource and interleaving bounds for the manager-sync side channel.

Task 11's evidence suite: the syncer must stay inside its designed
envelope no matter what the business traffic looks like. Each bound is
one independent test:

- **thousand-member roster** — a full 1,000-member Team Space projects
  to exactly one 1,000-id snapshot through the real SQLite reader and
  confirms (the roster cap is a resource fact: one bounded PUT);
- **multi-Space paging** — a Space fan-out across two Spaces with 250
  bots each expands 500 distinct per-Bot keys, always with the page
  clamp ≤ 100, delivering each key exactly once;
- **coalescing** — thousands of same-key notifications collapse into a
  single queue entry and (at most) one worker round;
- **worker width** — 4 workers never run more than 4 rounds in flight;
  the 5th+ key stays queued;
- **pool/shutdown** — a shutdown during a hung delivery cancels the
  round, keeps the unknown marker blocking, and leaves zero syncer
  threads behind;
- **queue capacity** — counts parked (running) rounds and pending Space
  hints against the one ``max_keys`` budget;
- **lease expiry vs marker** — the claim-status matrix over an expired
  lease only (marker BLOCKS, no marker reclaims); the deep marker
  survival semantics live in the plugin unit suite;
- **marker clear re-finish** — a successful CAS clear is not an
  idempotent wipe: re-finishing the same attempt refuses;
- **cross-tenant same Bot PK** — the same PK under two tenants is two
  independent keys and two independent delivery rounds.

Event/barrier-gated everywhere — no millisecond pass/fail thresholds.
"""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.bot_collaborator.manager_sync import (
    lifecycle as lifecycle_module,
)
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DispatcherPolicy,
)
from agentclaw.community.core.bot_collaborator.manager_sync.lifecycle import (
    BotManagerSyncLifecycle,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.repository.implementations.bot.manager_sync import (
    SqlManagerSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    CollaboratorFact,
    SpaceChange,
    SubmissionResult,
    SyncFacts,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.plugin_api.manager_sync_coordination import (
    ClaimStatus,
)
from agentclaw.community.plugins.local.bcs_managers import (
    RecordingBcsManagersPlugin,
)
from agentclaw.community.plugins.local.manager_sync_coordination import (
    MemoryManagerSyncCoordinator,
)
from tests.community.core.bot_collaborator.manager_sync.factories import (
    seed_members,
    seed_space,
    seed_space_bots,
    seed_sync_facts,
)


class _InMemorySqliteDB:
    """The reader suite's tiny SQLite fixture, local to this family.

    Secrets of the shape mirror ``plugins/local/database.py``:
    ``StaticPool`` shares ONE ``:memory:`` connection with every thread
    (the service and the dispatcher both read through ``to_thread``), so
    every session serializes on the instance lock — exactly the trade
    the local plugin makes for the same reason.
    """

    def __init__(self, engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(bind=engine, autoflush=False)
        self._session_lock = threading.RLock()

    @contextmanager
    def orm_session(self):
        with self._session_lock:
            db = self._session_factory()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()


@pytest.fixture
def db():
    # StaticPool: the service's ``to_thread`` reads run on another
    # thread, and plain :memory: would hand them an empty private DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return _InMemorySqliteDB(engine)


class FakeClock:
    """Frozen monotonic clock the tests advance explicitly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class GatedService:
    """A worker-facing consumer double that parks on a gate.

    ``gate`` closed means every round hangs inside the service (the
    callers under test have already answered); open means rounds confirm
    instantly. ``calls`` records each dispatched key, in order.
    """

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.calls: list[SyncKey] = []

    async def sync_once(self, key: SyncKey) -> SyncOutcome:
        self.calls.append(key)
        await self.gate.wait()
        return SyncOutcome.CONFIRMED

    async def hold_owner_check(self, key: SyncKey) -> SyncOutcome:
        self.calls.append(key)
        await self.gate.wait()
        return SyncOutcome.BLOCKED


def _facts(key: SyncKey) -> SyncFacts:
    return SyncFacts(
        key=key,
        bot_id=f"bot-{key.bot_pk}",
        entity_id=f"entity-{key.bot_pk}",
        owner_id="owner",
        collaborators=(CollaboratorFact("member", "admin", True),),
    )


def make_dispatcher(service, reader=None, *, policy=None) -> BotManagerSyncDispatcher:
    return BotManagerSyncDispatcher(
        service,
        reader if reader is not None else Mock(),
        policy=policy or DispatcherPolicy(),
    )


def _key(bot_pk: int) -> SyncKey:
    return SyncKey("a", "dev", bot_pk)


def _admit(dispatcher, bot_pk: int) -> SubmissionResult:
    return dispatcher.request_sync(
        bot_pk=bot_pk,
        env="dev",
        avernet_tenant="a",
        reason=SyncReason.COLLABORATOR_CHANGED,
    )


async def _spin_until(predicate, *, what: str, timeout: float = 5.0) -> None:
    """Yield until ``predicate()`` — a dead loop, not a race, safeties it."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"condition never became true: {what}"
        await asyncio.sleep(0)


# -- 1. a thousand-member roster, delivered as one bounded snapshot ---------------


@pytest.mark.asyncio
async def test_full_stack_projects_a_thousand_member_roster_to_bcs(db):
    """The 1,000-id cap is one bounded PUT, proven over real SQLite."""
    space_id = 301
    seed_space(db, space_id=space_id, env="dev")
    seed_members(
        db,
        space_id=space_id,
        env="dev",
        user_ids=[f"user-{i:04d}" for i in range(1_000)],
    )
    key = seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=401,
        entity_id="entity-401",
        owner_id="owner",
        rows=tuple((f"user-{i:04d}", "member") for i in range(1_000)),
        space_id=space_id,
    )
    plugin = RecordingBcsManagersPlugin()
    service = BotManagerSyncService(
        reader=SqlManagerSyncReadRepository(db),
        bcs=plugin,
        coordinator=MemoryManagerSyncCoordinator(),
    )

    assert await service.sync_once(key) is SyncOutcome.CONFIRMED

    delivered = plugin.calls[-1]
    assert len(delivered.manager_user_ids) == 1_000
    assert delivered.manager_user_ids == tuple(
        sorted(f"user-{i:04d}" for i in range(1_000))
    )


# -- 2. multi-Space fan-out paged under the clamp ----------------------------------


@pytest.mark.asyncio
async def test_space_fan_out_walks_two_spaces_page_bounded(db):
    """500 Bots across two Spaces: pages stay ≤100, each key once."""
    space_one, space_two = 311, 312
    for space_id, pks in (
        (space_one, list(range(3_000, 3_250))),
        (space_two, list(range(3_300, 3_550))),
    ):
        seed_space(db, space_id=space_id, env="dev")
        seed_space_bots(db, tenant="a", env="dev", space_id=space_id, bot_pks=pks)

    reader = SqlManagerSyncReadRepository(db)
    asked_limits: list[int] = []
    real_page_space_keys = reader.page_space_keys

    def _recording_page(change, *, after_pk, limit):
        asked_limits.append(int(limit))
        return real_page_space_keys(change, after_pk=after_pk, limit=limit)

    reader.page_space_keys = _recording_page  # recording wrapper, same body

    service = GatedService()
    service.gate.set()
    dispatcher = make_dispatcher(service, reader)
    await dispatcher.attach()
    try:
        for space_id in (space_one, space_two):
            assert (
                dispatcher.request_space_sync(
                    SpaceChange("a", "dev", (str(space_id),), ())
                )
                is SubmissionResult.QUEUED
            )
        assert await dispatcher.drain(30)
        walked = sorted(key.bot_pk for key in service.calls)
        assert walked == list(range(3_000, 3_250)) + list(range(3_300, 3_550))
        assert len(set(service.calls)) == 500  # no key walked twice
        assert asked_limits and all(limit <= 100 for limit in asked_limits)
        assert dispatcher.stats.entries == 0
    finally:
        await dispatcher.stop()


# -- 3. thousands of same-key notifications stay one entry -------------------------


@pytest.mark.asyncio
async def test_thousands_of_same_key_notifications_coalesce_into_one_entry():
    """5,000 bursts on one key: 1 entry, at most 1 round, 0 growth."""
    service = GatedService()  # gate closed: the single entry stays parked
    dispatcher = make_dispatcher(service)
    await dispatcher.attach()
    try:
        results = [_admit(dispatcher, 10) for _ in range(5_000)]

        assert results[0] is SubmissionResult.QUEUED
        assert all(result is SubmissionResult.COALESCED for result in results[1:])
        assert dispatcher.stats.entries == 1
        await _spin_until(lambda: bool(service.calls), what="the one round started")
        assert len(service.calls) == 1
    finally:
        service.gate.set()
        await dispatcher.stop()


# -- 4. four workers never exceed four in-flight rounds ----------------------------


@pytest.mark.asyncio
async def test_four_workers_cap_in_flight_rounds_at_four():
    """Eight parked keys, four workers: exactly 4 running, 4 queued."""
    service = GatedService()  # gate closed: rounds park
    dispatcher = make_dispatcher(service)
    await dispatcher.attach()
    try:
        assert [_admit(dispatcher, pk) for pk in range(1, 9)] == [
            SubmissionResult.QUEUED
        ] * 8

        await _spin_until(
            lambda: dispatcher.stats.running == 4, what="four rounds running"
        )
        stats = dispatcher.stats
        assert stats.running == 4
        assert stats.entries == 8  # running + queued share one budget view
        assert len(service.calls) == 4  # in flight only what workers hold

        service.gate.set()
        assert await dispatcher.drain(10)
        assert len(service.calls) == 8
        assert len(set(service.calls)) == 8
    finally:
        service.gate.set()
        await dispatcher.stop()


# -- 5. shutdown during a hung delivery: bounded teardown, marker kept ---------------


@pytest.mark.asyncio
async def test_shutdown_cancels_hung_round_keeps_marker_and_leaves_no_threads(
    monkeypatch,
):
    """The real lifecycle shuts a parked delivery down, failing closed."""
    plugin = RecordingBcsManagersPlugin()
    coordinator = MemoryManagerSyncCoordinator()
    reader = Mock()
    reader.read_facts.side_effect = _facts
    entered = threading.Event()
    real_replace = plugin.replace_managers

    async def _hanging_replace(snapshot, *, timeout_seconds):
        entered.set()
        await asyncio.sleep(3600)  # park the round on a cancellable wait
        return await real_replace(snapshot, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(plugin, "replace_managers", _hanging_replace)
    service = BotManagerSyncService(reader=reader, bcs=plugin, coordinator=coordinator)
    dispatcher = BotManagerSyncDispatcher(service, reader, env="dev")
    lifecycle = BotManagerSyncLifecycle(dispatcher=dispatcher, bcs=plugin)

    await lifecycle.startup()
    assert _admit(dispatcher, 501) is SubmissionResult.QUEUED
    await _spin_until(entered.is_set, what="the hung round reached the PUT")
    assert [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("manager-sync")
    ], "sanity: the real worker loop owns live threads"

    # Shutdown's drain is bounded by a constant the test shortens — a hung
    # delivery must not stretch teardown to production's full window.
    monkeypatch.setattr(lifecycle_module, "_SHUTDOWN_DRAIN_SECONDS", 0.1)
    await lifecycle.shutdown()

    assert not [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("manager-sync")
    ], "no manager-sync thread may outlive shutdown"
    assert not dispatcher.stats.attached

    # The cancelled round kept its unknown marker: a later round is blocked
    # at claim time, never replayed into a confirmed one.
    assert await service.sync_once(_key(501)) is SyncOutcome.BLOCKED


# -- 6. capacity counts running rounds and Space hints together ---------------------


@pytest.mark.asyncio
async def test_capacity_counts_space_hints_and_running_entries():
    """One budget: parked rounds hold slots and pending hints hold slots.

    A single worker parks on the first key deterministically, so the
    slot arithmetic below is a state assertion, not a race: a running
    entry (1) plus the first hint (1) fills ``max_keys=2`` and a second
    hint is refused from memory; after the round completes, the hint
    expands a three-key page into the budget, dropping exactly the keys
    that no longer fit on the rejected-keys counter.
    """
    service = GatedService()  # gate closed: the one worker parks on it
    reader = Mock()
    dispatcher = BotManagerSyncDispatcher(
        service,
        reader,
        policy=DispatcherPolicy(max_keys=2, worker_count=1),
    )
    await dispatcher.attach()
    try:
        assert _admit(dispatcher, 21) is SubmissionResult.QUEUED
        await _spin_until(
            lambda: dispatcher.stats.running == 1, what="the round parked"
        )
        # Slot 1: the parked round. Slot 2: the first Space hint. Full.
        hint_one = SpaceChange("a", "dev", ("301",), ())
        hint_two = SpaceChange("a", "dev", ("302",), ())
        assert dispatcher.request_space_sync(hint_one) is SubmissionResult.QUEUED
        assert dispatcher.request_space_sync(hint_two) is SubmissionResult.REJECTED

        # Release: the round finishes, the hint expands a page of three
        # keys into the now-free budget and drops exactly what no longer
        # fits onto the rejected-keys counter.
        reader.page_space_keys.side_effect = [[_key(31), _key(32), _key(33)]]
        service.gate.set()
        assert await dispatcher.drain(10)

        stats = dispatcher.stats
        assert stats.entries == 0
        assert stats.rejected_space_keys == 1  # _key(33) did not fit
        assert [key.bot_pk for key in service.calls] == [21, 31, 32]
    finally:
        service.gate.set()
        await dispatcher.stop()


async def _drain_bounded(dispatcher: BotManagerSyncDispatcher) -> None:
    assert await dispatcher.drain(10), "workers never drained the queue"


# -- 7. lease expiry vs the TTL-less in-flight marker -------------------------------


def test_lease_expiry_claim_matrix_keeps_the_marker_bound():
    """Only the claim-status matrix over an expired lease — see the
    plugin unit suite (``test_manager_sync_coordination.py``) for the
    marker survival / stale-holder semantics in depth."""
    clock = FakeClock()
    coordinator = MemoryManagerSyncCoordinator(clock=clock)
    claimed = coordinator.claim(_key(601), holder="holder-a", ttl_seconds=30)
    assert claimed.status is ClaimStatus.ACQUIRED
    coordinator.begin(claimed.lease, attempt_id="attempt-1", digest="digest-1")

    clock.advance(120)  # lease long expired; the marker has no TTL

    # With a marker: BLOCKED. Without one: the expired lease reclaims.
    assert (
        coordinator.claim(_key(601), holder="holder-b", ttl_seconds=30).status
        is ClaimStatus.BLOCKED
    )
    coordinator.claim(_key(602), holder="holder-a", ttl_seconds=5)
    clock.advance(6)
    assert (
        coordinator.claim(_key(602), holder="holder-b", ttl_seconds=30).status
        is ClaimStatus.ACQUIRED
    )


# -- 8. the marker clear is a three-way CAS -----------------------------------------


def test_marker_clear_is_not_an_idempotent_wipe_of_the_key():
    """Only the re-finish interleave — every wrong holder / attempt id /
    digest verdict is already pinned by the plugin unit suite
    (``test_manager_sync_coordination.py``)."""
    coordinator = MemoryManagerSyncCoordinator()
    claimed = coordinator.claim(_key(603), holder="holder-a", ttl_seconds=30)
    attempt = coordinator.begin(
        claimed.lease, attempt_id="attempt-1", digest="digest-1"
    )

    assert coordinator.finish(attempt) is True
    assert coordinator.read_blocked(_key(603)) is None
    # The successful clear is not a blanket wipe: re-finishing the very
    # same attempt must refuse — the marker is gone, and a second CAS
    # may not "succeed" against a state it already deleted.
    assert coordinator.finish(attempt) is False


# -- 9. the same Bot PK under two tenants is two independent keys -------------------


class TwoTenantReader:
    """Route each key's facts read to that tenant's own database.

    SQLite's ``ac_bots.id`` is a single-table primary key, so the two
    tenants' SAME Bot PK must live in two databases — which is also the
    production shape this bound pins: one tenant's rows are never the
    other tenant's facts, no matter the colliding PK.
    """

    def __init__(self, readers: dict[str, SqlManagerSyncReadRepository]) -> None:
        self._readers = readers

    def read_facts(self, key: SyncKey) -> SyncFacts:
        return self._readers[key.avernet_tenant].read_facts(key)


@pytest.mark.asyncio
async def test_cross_tenant_same_bot_pk_rounds_are_independent():
    """Two tenants, one PK: both deliver, neither blocks the other.

    Same PK, same env, one shared coordinator and one recording BCS —
    the two rounds must be fully independent keys.
    """
    readers: dict[str, SqlManagerSyncReadRepository] = {}
    for tenant, user in (("tenant-a", "user-a"), ("tenant-b", "user-b")):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        tenant_db = _InMemorySqliteDB(engine)
        key = seed_sync_facts(
            tenant_db,
            tenant=tenant,
            env="dev",
            bot_pk=7,
            entity_id="entity",
            owner_id="owner",
            rows=((user, "admin"),),
            space_id=None,
        )
        assert key.avernet_tenant == tenant
        readers[tenant] = SqlManagerSyncReadRepository(tenant_db)

    key_a = SyncKey("tenant-a", "dev", 7)
    key_b = SyncKey("tenant-b", "dev", 7)
    plugin = RecordingBcsManagersPlugin()
    service = BotManagerSyncService(
        reader=TwoTenantReader(readers),
        bcs=plugin,
        coordinator=MemoryManagerSyncCoordinator(),
    )

    assert await service.sync_once(key_a) is SyncOutcome.CONFIRMED
    assert await service.sync_once(key_b) is SyncOutcome.CONFIRMED

    assert [call.key for call in plugin.calls] == [key_a, key_b]
    assert plugin.calls[0].manager_user_ids == ("user-a",)
    assert plugin.calls[1].manager_user_ids == ("user-b",)
