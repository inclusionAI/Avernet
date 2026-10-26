"""First activation must serialize with source writers, not just other activators."""


from tests.community.core.space_knowledge.capture_fixtures import enabled_capture
from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import KnowledgeSpacePersistenceEffects
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from sqlalchemy import event

from agentclaw.community.core.repository.implementations.spaces import knowledge_sync
from agentclaw.community.core.repository.implementations.spaces.management import (
    SpaceManagementRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.work_orders.repository.models import WorkOrderModel
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
from agentclaw.community.plugins.community.database import CommunityDatabase
from tests.community.core.space_knowledge.test_membership_real_database import (
    BoundedSink,
    create_space,
    database as database,
    history,
    synchronizer,
)


@pytest.mark.parametrize("mutation", ["remove", "add", "delete", "auto"])
def test_activation_roster_and_source_mutation_are_one_serial_order(
    database, monkeypatch, mutation
):
    space_id, _ = create_space(database)
    SpaceRepository(database, KnowledgeSpacePersistenceEffects()).add_member(
        space_id=space_id,
        user_id="victim",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    if mutation == "auto":
        with database.orm_session() as session:
            order = WorkOrderModel(
                work_order_no="WO-race",
                biz_type="SPACE_JOIN",
                biz_id=str(space_id),
                applicant_user_id="new-member",
                status="PROCESSING",
                approval_mode="AUTO",
                env="pre",
            )
            session.add(order)
            session.flush()
            order_id = order.id
    other = CommunityDatabase(database._url)
    sink = BoundedSink()
    sync = synchronizer(database, sink)
    roster_read, release, write_attempted, committed = (Event() for _ in range(4))
    save = knowledge_sync._save_snapshot

    def pause_first_snapshot(db, **kwargs):
        if kwargs["body"]["revision"] == 1:
            roster_read.set()
            assert release.wait(timeout=10)
        return save(db, **kwargs)

    monkeypatch.setattr(knowledge_sync, "_save_snapshot", pause_first_snapshot)

    @event.listens_for(other._engine, "before_cursor_execute")
    def observe_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("UPDATE ", "DELETE ", "INSERT ")):
            write_attempted.set()

    def mutate():
        spaces = SpaceRepository(other, KnowledgeSpacePersistenceEffects())
        if mutation == "remove":
            assert spaces.delete_member(space_id=space_id, user_id="victim", env="pre")
        elif mutation == "add":
            spaces.add_member(
                space_id=space_id,
                user_id="new-member",
                role=SpaceRole.MEMBER,
                creator_id="owner",
                env="pre",
            )
        elif mutation == "delete":
            SpaceManagementRepository(other).delete_team(
                space_id=space_id, with_members=True, env="pre"
            )
        else:
            WorkOrderRepository(other, Mock()).complete_auto_approval(
                work_order_id=order_id,
                recipient_user_ids=["new-member"],
                source_event_type="SPACE_JOIN_REQUESTED",
                env="pre",
            )
        committed.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            activation = pool.submit(
                sync.ensure_synced, space_id=space_id, environment="pre"
            )
            try:
                assert roster_read.wait(timeout=5)
                change = pool.submit(mutate)
                assert write_attempted.wait(timeout=5)
                # Old implementation commits here without recording an event.
                # The repair holds a DB write guard, so this wait expires.
                committed.wait(timeout=0.5)
            finally:
                release.set()
            try:
                activation.result(timeout=10)
            except SpaceKnowledgeError as exc:
                # Deletion may win immediately after the first snapshot commits.
                assert mutation == "delete" and exc.code == "RESOURCE_NOT_FOUND"
            change.result(timeout=10)
    finally:
        release.set()
        other._engine.dispose()
    while sync.deliver_next(space_id=space_id, environment="pre"):
        pass
    current = history(database, space_id)[-1]
    expected = {"owner", "victim"} if mutation in {"add", "auto"} else {"owner"}
    if mutation in {"add", "auto"}:
        expected.add("new-member")
    if mutation == "delete":
        assert current["status"] == "disabled"
        expected = set()
    else:
        assert current["status"] == "active"
    assert {m["staff_no"] for m in current["members"]} == expected
    assert sync.progress(space_id=space_id, environment="pre") == (
        2,
        2,
        "disabled" if mutation == "delete" else "active",
    )


def test_source_deleted_before_activation_guard_is_not_resurrected(
    database, monkeypatch
):
    space_id, _ = create_space(database)
    before_guard, release = Event(), Event()
    lock_space = knowledge_sync.lock_space

    def pause_before_guard(*args, **kwargs):
        before_guard.set()
        assert release.wait(timeout=5)
        return lock_space(*args, **kwargs)

    monkeypatch.setattr(knowledge_sync, "lock_space", pause_before_guard)
    sink = BoundedSink()
    sync = synchronizer(database, sink)
    other = CommunityDatabase(database._url)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            activation = pool.submit(
                sync.ensure_synced, space_id=space_id, environment="pre"
            )
            try:
                assert before_guard.wait(timeout=5)
                SpaceManagementRepository(other).delete_team(
                    space_id=space_id, with_members=True, env="pre"
                )
            finally:
                release.set()
            with pytest.raises(SpaceKnowledgeError, match="RESOURCE_NOT_FOUND"):
                activation.result(timeout=5)
    finally:
        release.set()
        other._engine.dispose()
    assert history(database, space_id) == []
    assert sink.calls == []


def test_sqlite_guard_does_not_change_space_metadata(database):
    from datetime import datetime
    from agentclaw.community.core.spaces.repository.models import SpaceModel

    space_id, _ = create_space(database)
    fixed = datetime(2020, 1, 1)
    with database.orm_session() as session:
        session.get(SpaceModel, space_id).gmt_modified = fixed
    synchronizer(database, BoundedSink()).ensure_synced(
        space_id=space_id, environment="pre"
    )
    with database.orm_session() as session:
        assert session.get(SpaceModel, space_id).gmt_modified == fixed


def test_sqlite_guard_contention_propagates_bounded_failure(database):
    from sqlalchemy.exc import OperationalError

    space_id, _ = create_space(database)
    other = CommunityDatabase(database._url)

    @event.listens_for(other._engine, "connect")
    def short_busy_timeout(connection, record):
        connection.execute("PRAGMA busy_timeout=50")

    try:
        with database.transactional_orm_session() as session:
            knowledge_sync.lock_space(session, space_id, "pre")
            with pytest.raises(OperationalError, match="locked"):
                knowledge_sync.SpaceKnowledgeSyncRepository(other, enabled_capture(other)).activate(
                    space_id=space_id, environment="pre", app="agentclaw"
                )
    finally:
        other._engine.dispose()
    assert history(database, space_id) == []
