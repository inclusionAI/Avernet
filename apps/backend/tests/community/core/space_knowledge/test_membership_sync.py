"""Durable membership production using real SQLite transactions and queue records."""

from contextlib import contextmanager
from threading import RLock
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.spaces.management import (
    SpaceManagementRepository,
)
from agentclaw.community.core.repository.implementations.spaces.knowledge_sync import (
    SpaceKnowledgeSyncRepository,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.core.task_queue.repository.models import TaskQueueModel


class Database:
    def __init__(self, path):
        self.lock = RLock()
        self.engine = create_engine(f"sqlite:///{path}")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def transactional_orm_session(self):
        with self.lock, self.factory.begin() as session:
            yield session

    orm_session = transactional_orm_session


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / "tc.db")
    with db.orm_session() as s:
        s.add(
            SpaceModel(
                id=7,
                space_code="s7",
                space_type="TEAM",
                name="Team",
                env="pre",
                created_by="owner",
                updated_by="owner",
            )
        )
        s.add(
            SpaceMemberModel(
                space_id=7, user_id="owner", role="ADMIN", env="pre", created_by="owner"
            )
        )
    return (
        db,
        SpaceRepository(db),
        SpaceKnowledgeSyncRepository(db),
        SpaceManagementRepository(db),
    )


def tasks(db):
    with db.orm_session() as s:
        return [
            json.loads(r.payload)
            for r in s.query(TaskQueueModel).order_by(TaskQueueModel.id)
        ]


def test_no_activation_no_queue_then_full_first_snapshot(setup):
    db, spaces, sync, _ = setup
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    assert tasks(db) == []
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    snapshots = tasks(db)
    assert len(snapshots) == 1
    assert {m["staff_no"] for m in snapshots[0]["members"]} == {"owner", "alice"}
    assert snapshots[0]["revision"] == 1


def test_member_versions_and_noop_do_not_lose_remove_then_rejoin(setup):
    db, spaces, sync, _ = setup
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    spaces.update_member_role(
        space_id=7, user_id="alice", role=SpaceRole.MEMBER, env="pre"
    )
    spaces.delete_member(space_id=7, user_id="alice", env="pre")
    spaces.add_member(
        space_id=7, user_id="alice", role=SpaceRole.ADMIN, creator_id="owner", env="pre"
    )
    assert [p["revision"] for p in tasks(db)] == [1, 2, 3, 4]
    assert "alice" not in {m["staff_no"] for m in tasks(db)[2]["members"]}


def test_physical_delete_keeps_disabled_payload_and_independent_state(setup):
    db, _, sync, management = setup
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    management.delete_team(space_id=7, with_members=True, env="pre")
    with db.orm_session() as s:
        assert s.get(SpaceModel, 7) is None
        assert s.query(SpaceKnowledgeSyncModel).one().status == "disabled"
    assert tasks(db)[-1]["status"] == "disabled"


def test_transaction_failure_rolls_back_members_and_event(setup, monkeypatch):
    db, spaces, sync, _ = setup
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    from agentclaw.community.core.repository.implementations.spaces import (
        knowledge_sync,
    )

    def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(knowledge_sync, "_enqueue", fail)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        spaces.add_member(
            space_id=7,
            user_id="alice",
            role=SpaceRole.MEMBER,
            creator_id="owner",
            env="pre",
        )
    assert spaces.get_member(space_id=7, user_id="alice", env="pre") is None
    assert len(tasks(db)) == 1


class RecordingSink:
    def __init__(self):
        self.calls = []
        self.fail = False

    def is_enabled(self):
        return True

    def apply_snapshot(self, snapshot):
        self.calls.append(snapshot)
        if self.fail:
            raise RuntimeError("external diagnostic must not leak")


def coordinator(sync, sink):
    from agentclaw.community.core.space_knowledge.membership_sync import (
        SpaceMembershipSynchronizer,
    )

    return SpaceMembershipSynchronizer(sync, sink, "agentclaw")


def test_real_producer_first_confirmation_reads_and_terminal_repair(setup):
    db, spaces, repo, _ = setup
    sink = RecordingSink()
    sync = coordinator(repo, sink)
    assert sync.get_context(space_id=7, environment="pre") is None
    assert tasks(db) == []
    context = sync.ensure_synced(space_id=7, environment="pre")
    assert context.revision == 1
    assert len(sink.calls) == 1
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    assert sync.get_context(space_id=7, environment="pre").revision == 1
    assert len(sink.calls) == 1
    with db.orm_session() as s:
        row = (
            s.query(TaskQueueModel)
            .filter_by(idempotency_key="space:7:membership:2")
            .one()
        )
        row.status, row.active_idempotency_key = "TIMED_OUT", None
    # New repository + producer instance: no in-memory delivery state required.
    restarted = coordinator(SpaceKnowledgeSyncRepository(db), sink)
    restarted.repair_pending(environment="pre")
    assert len(tasks(db)) == 3
    restarted.deliver_next(space_id=7, environment="pre")
    assert repo.progress(space_id=7, environment="pre", app="agentclaw") == (
        2,
        2,
        "active",
    )


def test_failure_and_predecessor_order_do_not_lose_events(setup):
    db, spaces, repo, _ = setup
    sink = RecordingSink()
    sync = coordinator(repo, sink)
    sink.fail = True
    with pytest.raises(RuntimeError):
        sync.ensure_synced(space_id=7, environment="pre")
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    from agentclaw.community.core.space_knowledge.membership_tasks import (
        SpaceMembershipSyncTaskHandler,
    )
    from agentclaw.community.core.task_queue.types import Retry, Complete

    handler = SpaceMembershipSyncTaskHandler(sync, "pre")
    assert isinstance(handler.handle(tasks(db)[1]), Retry)
    assert len(sink.calls) == 1
    result = handler.handle(tasks(db)[0])
    assert isinstance(result, Retry) and "external diagnostic" not in result.error
    sink.fail = False
    assert isinstance(handler.handle(tasks(db)[0]), Complete)
    assert isinstance(handler.handle(tasks(db)[1]), Complete)
    assert [s.revision for s in sink.calls] == [1, 1, 1, 2]


def test_ack_cannot_skip_or_change_history_and_other_app_cannot_take_over(setup):
    db, spaces, repo, _ = setup
    from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
    from dataclasses import replace

    repo.activate(space_id=7, environment="pre", app="agentclaw")
    snapshot = repo.next_snapshot(space_id=7, environment="pre", app="agentclaw")
    with pytest.raises(SpaceKnowledgeError):
        repo.acknowledge(
            snapshot=replace(snapshot, body={**snapshot.body, "name": "forged"}),
            app="agentclaw",
        )
    with pytest.raises(SpaceKnowledgeError):
        repo.activate(space_id=7, environment="pre", app="other")
    assert repo.progress(space_id=7, environment="prod", app="agentclaw") is None
    assert repo.next_snapshot(space_id=7, environment="pre", app="other") is None
    assert len(tasks(db)) == 1


def test_batch_writes_roles_rename_and_noops(setup):
    db, _, repo, management = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    management.add_members(
        space_id=7,
        members={"alice": "Alice", "bob": "Bob"},
        actor_id="owner",
        env="pre",
    )
    management.add_members(
        space_id=7, members={"alice": "Alice"}, actor_id="owner", env="pre"
    )
    management.set_member_roles(
        space_id=7, user_ids=["alice", "bob"], role="ADMIN", env="pre"
    )
    management.set_member_roles(space_id=7, user_ids=["alice"], role="ADMIN", env="pre")
    management.rename_team(space_id=7, name="Renamed", actor_id="owner", env="pre")
    management.remove_members(space_id=7, user_ids=["alice", "bob"], env="pre")
    management.remove_members(space_id=7, user_ids=["alice"], env="pre")
    assert [p["revision"] for p in tasks(db)] == [1, 2, 3, 4, 5]
    assert tasks(db)[3]["name"] == "Renamed"


@pytest.mark.asyncio
async def test_lifecycle_registers_repairs_and_stops_symmetrically(setup):
    import asyncio
    from agentclaw.community.core.space_knowledge.membership_tasks import (
        SpaceMembershipSyncLifecycle,
    )
    from agentclaw.community.core.task_queue.services.registry import HandlerRegistry

    db, _, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    with db.orm_session() as s:
        row = s.query(TaskQueueModel).one()
        row.status, row.active_idempotency_key = "FAILED", None
    registry = HandlerRegistry()
    lifecycle = SpaceMembershipSyncLifecycle(
        registry, coordinator(repo, RecordingSink()), "pre"
    )
    await lifecycle.bootstrap()
    assert registry.get("space.membership.sync") is not None
    await lifecycle.startup()
    for _ in range(100):
        if len(tasks(db)) == 2:
            break
        await asyncio.sleep(0.01)
    assert len(tasks(db)) == 2
    await lifecycle.shutdown()
    assert lifecycle._task is None


def test_schema_is_independent_and_never_alters_space():
    from pathlib import Path

    assert not any(
        c.name.startswith("knowledge_") for c in SpaceModel.__table__.columns
    )
    assert list(SpaceKnowledgeSyncModel.__table__.foreign_keys) == []
    migration = (
        Path(__file__).parents[4]
        / "src/agentclaw/community/core/spaces/sql/2026_10_24_space_knowledge_sync.sql"
    )
    sql = migration.read_text()
    assert "ALTER TABLE ac_space" not in sql
    for column in SpaceKnowledgeSyncModel.__table__.columns:
        assert column.name in sql


def test_concurrent_first_use_and_changes_keep_all_revisions(setup):
    from concurrent.futures import ThreadPoolExecutor

    db, spaces, repo, _ = setup

    def change(index):
        repo.activate(space_id=7, environment="pre", app="agentclaw")
        spaces.add_member(
            space_id=7,
            user_id=f"member-{index}",
            role=SpaceRole.MEMBER,
            creator_id="owner",
            env="pre",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(change, range(8)))
    snapshots = tasks(db)
    assert [p["revision"] for p in snapshots] == list(range(1, 10))
    assert len(snapshots[-1]["members"]) == 9


def test_unrelated_space_creation_does_not_activate_sync(setup):
    db, spaces, _, _ = setup
    with spaces.create_team_transaction(
        name="New", creator_id="owner", creator_user_name="Owner", env="pre"
    ) as new_space:
        assert new_space.id != 7
    assert tasks(db) == []
    with db.orm_session() as s:
        assert s.query(SpaceKnowledgeSyncModel).count() == 0


def test_two_deliverers_may_repeat_one_version_but_cannot_skip(setup):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    db, _, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    barrier = Barrier(2)

    class RacingSink(RecordingSink):
        def apply_snapshot(self, snapshot):
            self.calls.append(snapshot)
            barrier.wait(timeout=3)

    sink = RacingSink()
    first = coordinator(repo, sink)
    second = coordinator(SpaceKnowledgeSyncRepository(db), sink)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(s.deliver_next, space_id=7, environment="pre")
            for s in (first, second)
        ]
        assert all(f.result() for f in futures)
    assert [s.revision for s in sink.calls] == [1, 1]
    assert repo.progress(space_id=7, environment="pre", app="agentclaw") == (
        1,
        1,
        "active",
    )
    assert len(tasks(db)) == 1


def test_large_roster_keeps_existing_text_queue_and_roundtrips_all_members(setup):
    db, _, repo, _ = setup
    with db.orm_session() as s:
        s.add_all(
            [
                SpaceMemberModel(
                    space_id=7,
                    user_id=f"member-{index:06d}-" + "x" * 45,
                    role="MEMBER",
                    env="pre",
                    created_by="owner",
                )
                for index in range(1999)
            ]
        )
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    raw = tasks(db)
    assert len(raw) > 1
    with db.orm_session() as s:
        assert all(len(r.payload.encode()) < 65535 for r in s.query(TaskQueueModel))
        for row in s.query(TaskQueueModel):
            row.status, row.active_idempotency_key = "TIMED_OUT", None
    repo.repair_pending(environment="pre", app="agentclaw", after_space_id=0, limit=32)
    assert len(tasks(db)) == 2 * len(raw)
    snapshot = repo.next_snapshot(space_id=7, environment="pre", app="agentclaw")
    assert len(snapshot.body["members"]) == 2000
    assert snapshot.revision == 1
    repo.acknowledge(snapshot=snapshot, app="agentclaw")
    assert repo.progress(space_id=7, environment="pre", app="agentclaw") == (
        1,
        1,
        "active",
    )


@pytest.mark.parametrize(
    "damage", ["missing", "base64", "scope", "revision", "count", "json"]
)
def test_broken_fragment_never_delivers_acknowledges_or_replaces_history(setup, damage):
    from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError

    db, _, repo, _ = setup
    with db.orm_session() as s:
        s.add_all(
            [
                SpaceMemberModel(
                    space_id=7,
                    user_id=f"member-{index:06d}-" + "x" * 45,
                    role="MEMBER",
                    env="pre",
                    created_by="owner",
                )
                for index in range(700)
            ]
        )
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    with db.orm_session() as s:
        rows = s.query(TaskQueueModel).order_by(TaskQueueModel.id).all()
        assert len(rows) > 1
        for row in rows:
            row.status, row.active_idempotency_key = "FAILED", None
        damaged = rows[1]
        if damage == "missing":
            s.delete(damaged)
        elif damage == "json":
            damaged.payload = "{broken"
        else:
            data = json.loads(damaged.payload)
            field, value = {
                "base64": ("_data", "not base64!"),
                "scope": ("_space_id", 99),
                "revision": ("revision", 99),
                "count": ("_parts", 999999999),
            }[damage]
            data[field] = value
            damaged.payload = json.dumps(data)
    before = len(tasks(db)) if damage != "json" else len(rows)
    sink = RecordingSink()
    with pytest.raises(SpaceKnowledgeError):
        coordinator(repo, sink).deliver_next(space_id=7, environment="pre")
    assert sink.calls == []
    repo.repair_pending(environment="pre", app="agentclaw", after_space_id=0, limit=32)
    with db.orm_session() as s:
        assert s.query(TaskQueueModel).count() == before
    assert repo.progress(space_id=7, environment="pre", app="agentclaw")[1] == 0


@pytest.mark.parametrize(
    "payload", ['{"_parts":1000000000}', '{"_parts":true}', "[]", "{broken"]
)
def test_malformed_primary_history_cannot_break_repair_page(setup, payload):
    from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError

    db, _, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    with db.orm_session() as s:
        row = s.query(TaskQueueModel).one()
        row.payload, row.status, row.active_idempotency_key = payload, "FAILED", None
    repo.repair_pending(environment="pre", app="agentclaw", after_space_id=0, limit=32)
    with pytest.raises(SpaceKnowledgeError):
        repo.next_snapshot(space_id=7, environment="pre", app="agentclaw")
    with db.orm_session() as s:
        assert s.query(TaskQueueModel).count() == 1


def test_first_use_rechecks_after_fourth_successful_delivery(setup):
    _, spaces, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    for user in ["alice", "bob", "carol"]:
        spaces.add_member(
            space_id=7,
            user_id=user,
            role=SpaceRole.MEMBER,
            creator_id="owner",
            env="pre",
        )
    sink = RecordingSink()
    assert (
        coordinator(repo, sink).ensure_synced(space_id=7, environment="pre").revision
        == 4
    )
    assert [s.revision for s in sink.calls] == [1, 2, 3, 4]
