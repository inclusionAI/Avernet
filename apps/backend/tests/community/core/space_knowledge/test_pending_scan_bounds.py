"""Real SQLite probes bound outbox work, not just returned result length."""

from contextlib import contextmanager
import json

import pytest
from sqlalchemy import event

from agentclaw.community.core.repository.implementations.spaces.knowledge_sync import (
    SpaceKnowledgeSyncRepository,
)
from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.plugin_api.space_membership import SpaceMembershipSnapshot
from agentclaw.community.plugins.community.database import CommunityDatabase
from tests.community.core.space_knowledge.capture_fixtures import enabled_capture


@pytest.fixture
def store(tmp_path):
    db = CommunityDatabase(f"sqlite:///{tmp_path / 'scan.db'}", create_schema=False)
    Snapshot.__table__.create(db._engine)
    repo = SpaceKnowledgeSyncRepository(db, enabled_capture(db))
    # Exclude the one-time lifecycle schema validation from recurring work.
    assert repo.progress(space_id=1, environment="pre", app="agentclaw") is None
    yield db, repo
    db._engine.dispose()


def row(space_id, revision=1, *, acknowledged=False, env="pre", app="agentclaw"):
    body = dict(
        revision=revision,
        full_snapshot=True,
        space_type="TEAM",
        name="Team",
        creator_staff_no="owner",
        personal_owner_staff_no=None,
        status="active",
        members=[dict(staff_no="owner", role="ADMIN")],
    )
    snapshot = SpaceMembershipSnapshot(env, space_id, body)
    return dict(
        env=env,
        app=app,
        space_id=space_id,
        revision=revision,
        payload=json.dumps(body),
        snapshot_hash=snapshot.digest(),
        status="active",
        acknowledged=acknowledged,
    )


def insert(db, rows):
    with db.transactional_orm_session() as session:
        session.execute(Snapshot.__table__.insert(), rows)


@contextmanager
def work(db):
    measured = {"steps": 0, "sql": []}
    connections = set()

    def opcode():
        measured["steps"] += 1
        return 0

    def capture(connection, _cursor, statement, _params, _context, _many):
        raw = connection.connection.driver_connection
        connections.add(raw)
        raw.set_progress_handler(opcode, 1)
        measured["sql"].append(statement.lower())

    event.listen(db._engine, "before_cursor_execute", capture)
    try:
        yield measured
    finally:
        event.remove(db._engine, "before_cursor_execute", capture)
        for raw in connections:
            raw.set_progress_handler(None, 0)


def scan(repo, *, after=0, limit=32):
    return repo.pending_revisions(
        environment="pre", app="agentclaw", after_space_id=after, limit=limit
    )


def test_deep_pending_history_has_bounded_candidate_work(store):
    db, repo = store
    growth = []
    previous = 0
    for depth in (1, 1000, 100000):
        insert(db, [row(1, revision) for revision in range(previous + 1, depth + 1)])
        with work(db) as measured:
            assert scan(repo) == [(1, 1)]
        growth.append(measured["steps"])
        previous = depth
    assert max(growth) < 5000, growth
    assert max(growth) < min(growth) * 5 + 1000, growth


def test_scan_has_hard_space_bound_continuation_and_covering_index(store):
    db, repo = store
    insert(db, [row(sid, rev) for sid in range(1, 36) for rev in range(1, 4)])
    insert(
        db, [row(100, env="prod"), row(101, app="other"), row(102, acknowledged=True)]
    )
    with work(db) as measured:
        assert scan(repo, limit=100000) == [(sid, 1) for sid in range(1, 33)]
    selects = [sql for sql in measured["sql"] if sql.lstrip().startswith("select")]
    assert len(selects) <= 33
    assert all("payload" not in sql and "group by" not in sql for sql in selects)
    assert scan(repo, after=32) == [(33, 1), (34, 1), (35, 1)]
    assert scan(repo, after=35) == []
    assert scan(repo, limit=0) == []
    # Safe, read-only plan evidence for the exact generated seek SQL.
    with db._engine.connect() as connection:
        plans = connection.exec_driver_sql(
            "EXPLAIN QUERY PLAN " + selects[0], ("pre", "agentclaw", 0, 1, 0)
        ).all()
    details = " ".join(str(plan) for plan in plans).lower()
    assert "covering index idx_space_sync_pending" in details
    assert "temp b-tree" not in details


class Queue:
    def __init__(self):
        self.calls = []

    def enqueue(self, **kwargs):
        self.calls.append(kwargs)


class Sink:
    def __init__(self):
        self.calls = []

    def is_enabled(self):
        return True

    def apply_snapshot(self, snapshot):
        self.calls.append(snapshot)


def test_dispatch_progress_and_ack_metadata_do_not_fetch_payload(store):
    db, repo = store
    insert(db, [row(1), row(1, 2)])
    queue, sink = Queue(), Sink()
    sync = SpaceMembershipSynchronizer(repo, sink, "agentclaw", queue)
    with work(db) as measured:
        assert sync.repair_pending(environment="pre") == 0
        assert repo.progress(space_id=1, environment="pre", app="agentclaw") == (
            2,
            0,
            "active",
        )
    assert sink.calls == []
    assert queue.calls[0]["payload"] == {"_space_id": 1, "revision": 1}
    assert not any("payload" in sql for sql in measured["sql"])
    with work(db) as delivery:
        snapshot = repo.next_snapshot(space_id=1, environment="pre", app="agentclaw")
    assert sum(".payload" in sql for sql in delivery["sql"]) == 1
    with work(db) as confirmation:
        repo.acknowledge(snapshot=snapshot, app="agentclaw")
    assert not any("payload" in sql for sql in confirmation["sql"])
    assert repo.progress(space_id=1, environment="pre", app="agentclaw") == (
        2,
        1,
        "active",
    )


def test_idle_and_acknowledged_scans_have_no_writes(store):
    db, repo = store
    insert(db, [row(1, revision, acknowledged=True) for revision in range(1, 1001)])
    for _ in range(2):
        with work(db) as measured:
            assert scan(repo) == []
        assert not any(
            sql.lstrip().startswith(("insert", "update", "delete"))
            for sql in measured["sql"]
        )
        assert measured["steps"] < 1000


@pytest.mark.parametrize("damage", ["hash", "status", "envelope"])
def test_ack_requires_exact_delivered_hash_status_and_full_envelope(store, damage):
    from dataclasses import replace
    from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError

    db, repo = store
    insert(db, [row(1)])
    snapshot = repo.next_snapshot(space_id=1, environment="pre", app="agentclaw")
    if damage == "envelope":
        snapshot = replace(snapshot, body={**snapshot.body, "full_snapshot": False})
    else:
        with db.transactional_orm_session() as session:
            target = session.get(Snapshot, ("pre", 1, 1))
            if damage == "hash":
                target.snapshot_hash = "0" * 64
            else:
                target.status = "disabled"
    with pytest.raises(SpaceKnowledgeError):
        repo.acknowledge(snapshot=snapshot, app="agentclaw")
    assert repo.progress(space_id=1, environment="pre", app="agentclaw")[1] == 0
