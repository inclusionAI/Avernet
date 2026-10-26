"""Single-table membership snapshots and unchanged generic queue scheduling."""


from tests.community.core.space_knowledge.capture_fixtures import enabled_capture
from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import KnowledgeSpacePersistenceEffects
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
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)


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
        SpaceRepository(db, KnowledgeSpacePersistenceEffects()),
        SpaceKnowledgeSyncRepository(db, enabled_capture(db)),
        SpaceManagementRepository(db, effects=KnowledgeSpacePersistenceEffects()),
    )


def snapshots(db):
    with db.orm_session() as s:
        return [
            json.loads(r.payload)
            for r in s.query(SpaceKnowledgeSyncModel).order_by(
                SpaceKnowledgeSyncModel.revision
            )
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
    assert snapshots(db) == []
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    recorded = snapshots(db)
    assert len(recorded) == 1
    assert {m["staff_no"] for m in recorded[0]["members"]} == {"owner", "alice"}
    assert recorded[0]["revision"] == 1


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
    assert [p["revision"] for p in snapshots(db)] == [1, 2, 3, 4]
    assert "alice" not in {m["staff_no"] for m in snapshots(db)[2]["members"]}


def test_physical_delete_keeps_disabled_payload_and_independent_state(setup):
    db, _, sync, management = setup
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    management.delete_team(space_id=7, with_members=True, env="pre")
    with db.orm_session() as s:
        assert s.get(SpaceModel, 7) is None
        assert (
            s.query(SpaceKnowledgeSyncModel)
            .order_by(SpaceKnowledgeSyncModel.revision.desc())
            .first()
            .status
            == "disabled"
        )
    assert snapshots(db)[-1]["status"] == "disabled"


def test_transaction_failure_rolls_back_members_and_event(setup, monkeypatch):
    db, spaces, sync, _ = setup
    sync.activate(space_id=7, environment="pre", app="agentclaw")
    from agentclaw.community.core.repository.implementations.spaces import (
        knowledge_sync,
    )

    def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(knowledge_sync, "_save_snapshot", fail)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        spaces.add_member(
            space_id=7,
            user_id="alice",
            role=SpaceRole.MEMBER,
            creator_id="owner",
            env="pre",
        )
    assert spaces.get_member(space_id=7, user_id="alice", env="pre") is None
    assert len(snapshots(db)) == 1


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

    return SpaceMembershipSynchronizer(
        sync, sink, "agentclaw", TaskQueueRepository(sync._db)
    )


def test_real_producer_first_confirmation_reads_and_terminal_repair(setup):
    db, spaces, repo, _ = setup
    sink = RecordingSink()
    sync = coordinator(repo, sink)
    assert sync.get_context(space_id=7, environment="pre") is None
    assert snapshots(db) == []
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
    sync.repair_pending(environment="pre")
    with db.orm_session() as s:
        row = (
            s.query(TaskQueueModel)
            .filter_by(idempotency_key="space:agentclaw:7:membership:2")
            .one()
        )
        row.status, row.active_idempotency_key = "TIMED_OUT", None
    # New repository + producer instance: no in-memory delivery state required.
    restarted = coordinator(SpaceKnowledgeSyncRepository(db, enabled_capture(db)), sink)
    restarted.repair_pending(environment="pre")
    assert len(snapshots(db)) == 2
    with db.orm_session() as s:
        assert s.query(TaskQueueModel).count() == 2
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
    assert isinstance(handler.handle({"_space_id": 7, "revision": 2}), Retry)
    assert len(sink.calls) == 1
    result = handler.handle({"_space_id": 7, "revision": 1})
    assert isinstance(result, Retry) and "external diagnostic" not in result.error
    sink.fail = False
    assert isinstance(handler.handle({"_space_id": 7, "revision": 1}), Complete)
    assert isinstance(handler.handle({"_space_id": 7, "revision": 2}), Complete)
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
    assert len(snapshots(db)) == 1


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
    assert [p["revision"] for p in snapshots(db)] == [1, 2, 3, 4, 5]
    assert snapshots(db)[3]["name"] == "Renamed"


@pytest.mark.asyncio
async def test_lifecycle_registers_repairs_and_stops_symmetrically(setup):
    import asyncio
    from agentclaw.community.core.space_knowledge.membership_tasks import (
        SpaceMembershipSyncLifecycle,
    )
    from agentclaw.community.core.task_queue.services.registry import HandlerRegistry

    db, _, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    coordinator(repo, RecordingSink()).repair_pending(environment="pre")
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
        with db.orm_session() as s:
            if s.query(TaskQueueModel).count() == 2:
                break
        await asyncio.sleep(0.01)
    with db.orm_session() as s:
        assert s.query(TaskQueueModel).count() == 2
    assert len(snapshots(db)) == 1
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
    recorded = snapshots(db)
    assert [p["revision"] for p in recorded] == list(range(1, 10))
    assert len(recorded[-1]["members"]) == 9


def test_unrelated_space_creation_does_not_activate_sync(setup):
    db, spaces, _, _ = setup
    with spaces.create_team_transaction(
        name="New", creator_id="owner", creator_user_name="Owner", env="pre"
    ) as new_space:
        assert new_space.id != 7
    assert snapshots(db) == []
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
    second = coordinator(SpaceKnowledgeSyncRepository(db, enabled_capture(db)), sink)
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
    assert len(snapshots(db)) == 1


def test_large_roster_uses_one_mediumtext_snapshot_and_small_queue_pointer(setup):
    db, _, repo, _ = setup
    with db.orm_session() as s:
        s.add_all(
            [
                SpaceMemberModel(
                    space_id=7,
                    user_id=f"member-{i:06d}-" + "x" * 45,
                    role="MEMBER",
                    env="pre",
                    created_by="owner",
                )
                for i in range(1999)
            ]
        )
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    with db.orm_session() as s:
        row = s.query(SpaceKnowledgeSyncModel).one()
        assert len(row.payload.encode()) > 65535
        assert s.query(TaskQueueModel).count() == 0
    sync = coordinator(repo, RecordingSink())
    sync.repair_pending(environment="pre")
    with db.orm_session() as s:
        task = s.query(TaskQueueModel).one()
        assert json.loads(task.payload) == {"_space_id": 7, "revision": 1}
        assert len(task.payload.encode()) < 100
        # Terminal history may be purged normally; no member data is lost.
        s.delete(task)
    restarted = coordinator(SpaceKnowledgeSyncRepository(db, enabled_capture(db)), RecordingSink())
    restarted.repair_pending(environment="pre")
    snapshot = repo.next_snapshot(space_id=7, environment="pre", app="agentclaw")
    assert len(snapshot.body["members"]) == 2000
    repo.acknowledge(snapshot=snapshot, app="agentclaw")
    assert repo.progress(space_id=7, environment="pre", app="agentclaw") == (
        1,
        1,
        "active",
    )
    assert len(snapshots(db)) == 1


@pytest.mark.parametrize(
    "damage",
    ["missing", "hash", "revision", "status", "json", "members", "full_snapshot"],
)
def test_corrupt_or_missing_snapshot_never_delivers_or_replaces_history(setup, damage):
    from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError

    db, spaces, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    with db.orm_session() as s:
        row = s.query(SpaceKnowledgeSyncModel).filter_by(revision=1).one()
        if damage == "missing":
            s.delete(row)
        elif damage == "hash":
            row.snapshot_hash = "0" * 64
        elif damage == "json":
            row.payload = "{broken"
        else:
            body = json.loads(row.payload)
            body[damage] = {
                "revision": 99,
                "status": "disabled",
                "members": [],
                "full_snapshot": False,
            }[damage]
            row.payload = json.dumps(body)
    sink = RecordingSink()
    sync = coordinator(repo, sink)
    with pytest.raises(SpaceKnowledgeError):
        sync.deliver_next(space_id=7, environment="pre")
    sync.repair_pending(environment="pre")
    assert sink.calls == []
    assert repo.progress(space_id=7, environment="pre", app="agentclaw")[1] == 0
    with db.orm_session() as s:
        assert s.query(TaskQueueModel).count() == 0
        assert s.query(SpaceKnowledgeSyncModel).count() == (
            1 if damage == "missing" else 2
        )


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


def test_snapshots_commit_without_any_queue_table(setup):
    db, spaces, repo, _ = setup
    TaskQueueModel.__table__.drop(db.engine)
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    with db.orm_session() as session:
        rows = (
            session.query(SpaceKnowledgeSyncModel)
            .order_by(SpaceKnowledgeSyncModel.revision)
            .all()
        )
        assert [row.revision for row in rows] == [1, 2]
        assert all(not row.acknowledged for row in rows)
        assert "alice" not in {
            m["staff_no"] for m in json.loads(rows[0].payload)["members"]
        }
        assert "alice" in {
            m["staff_no"] for m in json.loads(rows[1].payload)["members"]
        }


def test_shared_queue_has_no_knowledge_specific_index():
    assert "idx_task_queue_sync_history" not in {
        index.name for index in TaskQueueModel.__table__.indexes
    }


def test_queue_unavailable_does_not_rollback_or_lose_membership_snapshots(setup):
    from agentclaw.community.core.space_knowledge.membership_sync import (
        SpaceMembershipSynchronizer,
    )

    db, spaces, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")

    class UnavailableQueue:
        def enqueue(self, **kwargs):
            raise RuntimeError("queue unavailable")

    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    failed = SpaceMembershipSynchronizer(
        repo, RecordingSink(), "agentclaw", UnavailableQueue()
    )
    failed.repair_pending(environment="pre")
    assert spaces.get_member(space_id=7, user_id="alice", env="pre") is not None
    assert [p["revision"] for p in snapshots(db)] == [1, 2]
    restarted = coordinator(SpaceKnowledgeSyncRepository(db, enabled_capture(db)), RecordingSink())
    restarted.repair_pending(environment="pre")
    restarted.repair_pending(environment="pre")
    with db.orm_session() as s:
        # The existing active-key index deduplicates without a new index.
        assert s.query(TaskQueueModel).count() == 1
    assert restarted.deliver_next(space_id=7, environment="pre")
    assert restarted.deliver_next(space_id=7, environment="pre")
    assert repo.progress(space_id=7, environment="pre", app="agentclaw") == (
        2,
        2,
        "active",
    )


def test_acknowledgement_loss_replays_identical_snapshot_before_next_version(
    setup, monkeypatch
):
    db, spaces, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    sink = RecordingSink()
    sync = coordinator(repo, sink)
    with monkeypatch.context() as patch:
        patch.setattr(
            repo,
            "acknowledge",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("ack lost")),
        )
        with pytest.raises(RuntimeError, match="ack lost"):
            sync.deliver_next(space_id=7, environment="pre")
    assert repo.progress(space_id=7, environment="pre", app="agentclaw")[1] == 0
    sync.deliver_next(space_id=7, environment="pre")
    sync.deliver_next(space_id=7, environment="pre")
    assert [s.revision for s in sink.calls] == [1, 1, 2]
    assert sink.calls[0].digest() == sink.calls[1].digest()


def test_pending_scan_is_scoped_bounded_and_does_not_page_each_revision(setup):
    db, spaces, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    for user in ["alice", "bob", "carol"]:
        spaces.add_member(
            space_id=7,
            user_id=user,
            role=SpaceRole.MEMBER,
            creator_id="owner",
            env="pre",
        )
    assert repo.pending_revisions(
        environment="pre", app="agentclaw", after_space_id=0, limit=1
    ) == [(7, 1)]
    assert (
        repo.pending_revisions(
            environment="prod", app="agentclaw", after_space_id=0, limit=1
        )
        == []
    )
    assert (
        repo.pending_revisions(
            environment="pre", app="other", after_space_id=0, limit=1
        )
        == []
    )
    assert (
        repo.pending_revisions(
            environment="pre", app="agentclaw", after_space_id=7, limit=1
        )
        == []
    )


def test_migration_creates_only_the_outbox_and_supports_mysql_large_payload():
    import re
    from pathlib import Path
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import mysql

    sql = (
        Path(__file__).parents[4]
        / "src/agentclaw/community/core/spaces/sql/2026_10_24_space_knowledge_sync.sql"
    ).read_text()
    assert re.findall(r"CREATE TABLE(?: IF NOT EXISTS)? (\w+)", sql) == [
        "ac_space_knowledge_sync"
    ]
    assert not re.search(r"(?:ALTER TABLE|CREATE INDEX)\s+", sql, re.I)
    assert "ac_space_knowledge_sync_snapshot" not in Base.metadata.tables
    assert "MEDIUMTEXT" in str(
        CreateTable(SpaceKnowledgeSyncModel.__table__).compile(dialect=mysql.dialect())
    )
    assert [c.name for c in SpaceKnowledgeSyncModel.__table__.primary_key] == [
        "env",
        "space_id",
        "revision",
    ]


def test_acknowledgement_cannot_skip_an_existing_predecessor(setup):
    from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
    from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot

    db, spaces, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    spaces.add_member(
        space_id=7,
        user_id="alice",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    with pytest.raises(SpaceKnowledgeError):
        repo.acknowledge(
            snapshot=SpaceMembershipSnapshot("pre", 7, snapshots(db)[1]),
            app="agentclaw",
        )
    assert repo.progress(space_id=7, environment="pre", app="agentclaw")[1] == 0


def test_full_pending_page_advances_by_space_and_returns_to_start(setup):
    db, spaces, repo, _ = setup
    for index in range(33):
        with spaces.create_team_transaction(
            name=f"Team-{index}",
            creator_id="owner",
            creator_user_name="Owner",
            env="pre",
        ) as space:
            sid = space.id
        repo.activate(space_id=sid, environment="pre", app="agentclaw")
    sync = coordinator(repo, RecordingSink())
    cursor = sync.repair_pending(environment="pre")
    assert cursor > 0
    assert sync.repair_pending(environment="pre", after_space_id=cursor) == 0
    with db.orm_session() as s:
        assert s.query(TaskQueueModel).count() == 33
    sync.repair_pending(environment="pre")
    with db.orm_session() as s:
        assert s.query(TaskQueueModel).count() == 33


def test_queue_queries_use_active_key_not_historical_idempotency_key(setup):
    from sqlalchemy import event

    db, _, repo, _ = setup
    repo.activate(space_id=7, environment="pre", app="agentclaw")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        sync = coordinator(repo, RecordingSink())
        sync.repair_pending(environment="pre")
        sync.repair_pending(environment="pre")
        sync.deliver_next(space_id=7, environment="pre")
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    selects = [
        q
        for q in statements
        if q.lstrip().startswith("select") and "from ac_task_queue" in q
    ]
    assert selects
    # On MySQL, IS TRUE/FALSE can force sorting over the version history;
    # exact boolean equality uses the new outbox covering index end to end.
    assert not any("ac_space_knowledge_sync.acknowledged is" in q for q in statements)
    for query in selects:
        where = query.split("where", 1)[1]
        assert "ac_task_queue.idempotency_key" not in where
        assert (
            "ac_task_queue.active_idempotency_key" in where
            or "ac_task_queue.id =" in where
        )
