"""Resource bounds for projection and local dispatch."""

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
from agentclaw.community.core.bot_collaborator.manager_sync.dispatcher import (
    BotManagerSyncDispatcher,
    DispatcherPolicy,
)
from agentclaw.community.core.bot_collaborator.manager_sync.service import (
    BotManagerSyncService,
)
from agentclaw.community.core.repository.implementations.bot.manager_sync import (
    SqlManagerSyncReadRepository,
)
from agentclaw.community.kernel.bot_manager_sync import (
    SpaceChange,
    SubmissionResult,
    SyncFacts,
    SyncKey,
    SyncOutcome,
    SyncReason,
)
from agentclaw.community.plugins.local.bcs_managers import RecordingBcsManagersPlugin
from tests.community.core.bot_collaborator.manager_sync.factories import (
    seed_members,
    seed_space,
    seed_space_bots,
    seed_sync_facts,
)


class MemoryDb:
    def __init__(self, engine) -> None:
        self.engine = engine
        self._factory = sessionmaker(bind=engine, autoflush=False)
        self._lock = threading.RLock()

    @contextmanager
    def orm_session(self):
        with self._lock:
            session = self._factory()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return MemoryDb(engine)


class GatedService:
    def __init__(self) -> None:
        self.calls: list[SyncKey] = []
        self.active = 0
        self.max_active = 0
        self.release = asyncio.Event()

    async def sync_once(self, key):
        self.calls.append(key)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await self.release.wait()
            return SyncOutcome.CONFIRMED
        finally:
            self.active -= 1


def submit(dispatcher, bot_pk: int) -> SubmissionResult:
    return dispatcher.request_sync(
        bot_pk=bot_pk,
        env="dev",
        avernet_tenant="a",
        reason=SyncReason.COLLABORATOR_CHANGED,
    )


async def spin_until(predicate, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_real_reader_projects_exactly_one_thousand_managers(db):
    seed_space(db, space_id=301, env="dev")
    users = [f"user-{index:04d}" for index in range(1_000)]
    seed_members(db, space_id=301, env="dev", user_ids=users)
    key = seed_sync_facts(
        db,
        tenant="a",
        env="dev",
        bot_pk=401,
        entity_id="entity-401",
        owner_id="owner",
        rows=tuple((user, "member") for user in users),
        space_id=301,
    )
    plugin = RecordingBcsManagersPlugin()
    service = BotManagerSyncService(SqlManagerSyncReadRepository(db), plugin)

    assert await service.sync_once(key) is SyncOutcome.CONFIRMED
    assert len(plugin.calls) == 1
    assert len(plugin.calls[0].manager_user_ids) == 1_000


@pytest.mark.asyncio
async def test_space_fanout_pages_at_most_one_hundred(db):
    seed_space(db, space_id=302, env="dev")
    seed_space_bots(
        db,
        tenant="a",
        env="dev",
        space_id=302,
        bot_pks=range(1, 206),
    )
    reader = SqlManagerSyncReadRepository(db)
    limits: list[int] = []
    original = reader.page_space_keys

    def recording_page(change, *, after_pk, limit):
        limits.append(limit)
        return original(change, after_pk=after_pk, limit=limit)

    reader.page_space_keys = recording_page
    plugin = RecordingBcsManagersPlugin()
    service = BotManagerSyncService(reader, plugin)
    dispatcher = BotManagerSyncDispatcher(
        service,
        reader,
        policy=DispatcherPolicy(max_keys=512, worker_count=4, page_size=100),
    )
    await dispatcher.attach()
    try:
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "dev", ("302",), ()))
            is SubmissionResult.QUEUED
        )
        assert await dispatcher.drain(20)
        assert len(plugin.calls) == 205
        assert limits == [100, 100, 100]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_thousands_of_same_key_notifications_use_one_entry_and_round():
    service = GatedService()
    service.release.set()
    dispatcher = BotManagerSyncDispatcher(service, Mock())
    assert submit(dispatcher, 7) is SubmissionResult.QUEUED
    for _ in range(5_000):
        assert submit(dispatcher, 7) is SubmissionResult.COALESCED
    assert dispatcher.stats.entries == 1
    await dispatcher.attach()
    try:
        assert await dispatcher.drain(5)
        assert service.calls == [SyncKey("a", "dev", 7)]
    finally:
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_four_workers_never_exceed_four_active_rounds():
    service = GatedService()
    dispatcher = BotManagerSyncDispatcher(
        service,
        Mock(),
        policy=DispatcherPolicy(worker_count=4, max_keys=20),
    )
    await dispatcher.attach()
    try:
        for bot_pk in range(1, 13):
            assert submit(dispatcher, bot_pk) is SubmissionResult.QUEUED
        await spin_until(lambda: service.active == 4)
        assert service.max_active == 4
        assert dispatcher.stats.running == 4
        assert dispatcher.stats.entries == 12
        service.release.set()
        assert await dispatcher.drain(5)
        assert len(service.calls) == 12
        assert service.max_active == 4
    finally:
        service.release.set()
        await dispatcher.stop()


@pytest.mark.asyncio
async def test_capacity_counts_running_keys_and_pending_space_hints():
    service = GatedService()
    dispatcher = BotManagerSyncDispatcher(
        service,
        Mock(),
        policy=DispatcherPolicy(worker_count=1, max_keys=2),
    )
    await dispatcher.attach()
    try:
        assert submit(dispatcher, 1) is SubmissionResult.QUEUED
        await spin_until(lambda: dispatcher.stats.running == 1)
        assert (
            dispatcher.request_space_sync(SpaceChange("a", "dev", ("space",), ()))
            is SubmissionResult.QUEUED
        )
        assert submit(dispatcher, 2) is SubmissionResult.REJECTED
        assert dispatcher.stats.entries + dispatcher.stats.space_hints == 2
    finally:
        service.release.set()
        await dispatcher.stop()


class TenantReader:
    def __init__(self, facts_by_key: dict[SyncKey, SyncFacts]) -> None:
        self.facts_by_key = facts_by_key

    def read_facts(self, key):
        return self.facts_by_key[key]


@pytest.mark.asyncio
async def test_same_bot_pk_in_two_tenants_delivers_independently():
    key_a = SyncKey("tenant-a", "dev", 7)
    key_b = SyncKey("tenant-b", "dev", 7)
    facts_by_key = {
        key_a: SyncFacts(key_a, "bot", "entity", "owner", ()),
        key_b: SyncFacts(key_b, "bot", "entity", "owner", ()),
    }
    plugin = RecordingBcsManagersPlugin()
    service = BotManagerSyncService(TenantReader(facts_by_key), plugin)
    assert await service.sync_once(key_a) is SyncOutcome.ACKNOWLEDGED_EMPTY
    assert await service.sync_once(key_b) is SyncOutcome.ACKNOWLEDGED_EMPTY
    assert [call.key for call in plugin.calls] == [key_a, key_b]
