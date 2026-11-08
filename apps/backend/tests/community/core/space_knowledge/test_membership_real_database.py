"""Regression tests use deployable file SQLite, never the fixture's RLock."""


from tests.community.core.space_knowledge.capture_fixtures import enabled_capture
from agentclaw.community.core.repository.implementations.spaces.knowledge_effects import KnowledgeSpacePersistenceEffects
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.spaces import knowledge_sync
from agentclaw.community.core.repository.implementations.spaces.knowledge_sync import (
    SpaceKnowledgeSyncRepository,
)
from agentclaw.community.core.repository.implementations.spaces.management import (
    SpaceManagementRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.platform.task_queue import (
    TaskQueueRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.space_knowledge.membership_sync import (
    SpaceMembershipSynchronizer,
)
from agentclaw.community.core.space_knowledge.repository.models import (
    SpaceKnowledgeSyncModel as Snapshot,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderDecision,
    WorkOrderNotificationDraft,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.space_knowledge import SpaceKnowledgeError
from agentclaw.community.plugins.community.database import CommunityDatabase


@pytest.fixture
def database(tmp_path):
    db = CommunityDatabase(f"sqlite:///{tmp_path / 'membership.db'}")
    Base.metadata.create_all(db._engine)
    yield db
    db._engine.dispose()


def create_space(db, *, owner="owner", personal=False, env="pre"):
    spaces = SpaceRepository(db, KnowledgeSpacePersistenceEffects())
    if personal:
        transaction = spaces.create_personal_transaction(
            user_id=owner, creator_user_name=None, env=env
        )
    else:
        transaction = spaces.create_team_transaction(
            name="Team", creator_id=owner, creator_user_name=None, env=env
        )
    with transaction as space:
        return space.id, space.space_code


def populate(db, space_id, count, *, wide=False):
    with db.transactional_orm_session() as session:
        session.add_all(
            [
                SpaceMemberModel(
                    space_id=space_id,
                    user_id=(f"{i:04d}" + "😀" * 124) if wide else f"member-{i:04d}",
                    role="MEMBER",
                    env="pre",
                    created_by="owner",
                )
                for i in range(count - 1)
            ]
        )


def history(db, space_id, env="pre"):
    with db.orm_session() as session:
        return [
            json.loads(row.payload)
            for row in session.query(Snapshot)
            .filter_by(env=env, space_id=space_id)
            .order_by(Snapshot.revision)
        ]


class BoundedSink:
    def __init__(self):
        self.calls = []

    def is_enabled(self):
        return True

    def apply_snapshot(self, snapshot):
        assert len(snapshot.body["members"]) <= 2000
        assert (
            len(json.dumps(snapshot.body, ensure_ascii=False).encode()) <= 1024 * 1024
        )
        self.calls.append(snapshot)


def synchronizer(db, sink):
    return SpaceMembershipSynchronizer(
        SpaceKnowledgeSyncRepository(db, enabled_capture(db)), sink, "agentclaw", TaskQueueRepository(db)
    )


@pytest.mark.parametrize("count,wide", [(2001, False), (2000, True)])
def test_first_activation_rejects_unreceivable_roster_without_outbox(
    database, count, wide
):
    space_id, _ = create_space(database)
    populate(database, space_id, count, wide=wide)
    sink = BoundedSink()
    with pytest.raises(SpaceKnowledgeError, match="SPACE_MEMBERSHIP_LIMIT_EXCEEDED"):
        synchronizer(database, sink).ensure_synced(space_id=space_id, environment="pre")
    assert history(database, space_id) == []
    assert sink.calls == []
    assert (
        SpaceRepository(database, KnowledgeSpacePersistenceEffects()).list_members(
            space_id=space_id, env="pre", keyword=None, offset=0, limit=1
        )[0]
        == count
    )


@pytest.mark.parametrize("batch", [False, True])
def test_limit_rejection_is_atomic_and_remove_rejoin_disable_can_drain(database, batch):
    space_id, _ = create_space(database)
    populate(database, space_id, 2000)
    sink = BoundedSink()
    sync = synchronizer(database, sink)
    sync.ensure_synced(space_id=space_id, environment="pre")
    spaces, management = SpaceRepository(database, KnowledgeSpacePersistenceEffects()), SpaceManagementRepository(database, effects=KnowledgeSpacePersistenceEffects())
    with pytest.raises(SpaceKnowledgeError, match="SPACE_MEMBERSHIP_LIMIT_EXCEEDED"):
        if batch:
            management.add_members(
                space_id=space_id,
                members={"overflow": None, "overflow2": None},
                actor_id="owner",
                env="pre",
            )
        else:
            spaces.add_member(
                space_id=space_id,
                user_id="overflow",
                role=SpaceRole.MEMBER,
                creator_id="owner",
                env="pre",
            )
    assert spaces.get_member(space_id=space_id, user_id="overflow", env="pre") is None
    assert spaces.get_member(space_id=space_id, user_id="overflow2", env="pre") is None
    assert len(history(database, space_id)) == 1
    spaces.delete_member(space_id=space_id, user_id="member-0000", env="pre")
    spaces.add_member(
        space_id=space_id,
        user_id="member-0000",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    management.delete_team(space_id=space_id, with_members=True, env="pre")
    while sync.deliver_next(space_id=space_id, environment="pre"):
        pass
    assert [snapshot.revision for snapshot in sink.calls] == [1, 2, 3, 4]
    assert "member-0000" not in {m["staff_no"] for m in sink.calls[1].body["members"]}
    assert "member-0000" in {m["staff_no"] for m in sink.calls[2].body["members"]}
    assert sync.progress(space_id=space_id, environment="pre") == (4, 4, "disabled")


@pytest.mark.parametrize(
    "entrypoint", ["process_approval", "review_space_join", "auto"]
)
def test_overlimit_approval_rolls_back_members_order_approver_and_notices(
    database, entrypoint
):
    space_id, _ = create_space(database)
    populate(database, space_id, 2000)
    synchronizer(database, BoundedSink()).ensure_synced(
        space_id=space_id, environment="pre"
    )
    status = "PROCESSING" if entrypoint == "auto" else "PENDING"
    with database.orm_session() as session:
        order = WorkOrderModel(
            work_order_no="WO-limit",
            biz_type="SPACE_JOIN",
            biz_id=str(space_id),
            applicant_user_id="overflow",
            status=status,
            approval_mode="AUTO" if entrypoint == "auto" else "MANUAL",
            env="pre",
        )
        session.add(order)
        session.flush()
        order_id = order.id
        session.add(
            WorkOrderApproverModel(
                work_order_id=order_id,
                approver_user_id="owner",
                status="PENDING",
                env="pre",
            )
        )
    repo = WorkOrderRepository(database, Mock(), effects=KnowledgeSpacePersistenceEffects())
    with pytest.raises(SpaceKnowledgeError, match="SPACE_MEMBERSHIP_LIMIT_EXCEEDED"):
        if entrypoint == "auto":
            repo.complete_auto_approval(
                work_order_id=order_id,
                recipient_user_ids=["overflow"],
                source_event_type="SPACE_JOIN_REQUESTED",
                env="pre",
            )
        elif entrypoint == "process_approval":
            repo.process_approval(
                work_order_id=order_id,
                reviewer_user_id="owner",
                decision=WorkOrderDecision.APPROVED,
                review_remark=None,
                env="pre",
            )
        else:
            repo.review_space_join(
                work_order_id=order_id,
                reviewer_user_id="owner",
                target_status=WorkOrderStatus.APPROVED,
                review_remark=None,
                applicant_user_name="Overflow",
                env="pre",
                notification=WorkOrderNotificationDraft(
                    recipient_user_id="overflow",
                    notification_category=NotificationCategory.NOTICE,
                    event_type="SPACE_JOIN_REVIEWED",
                    biz_type="SPACE_JOIN",
                    biz_id=str(space_id),
                    title="Reviewed",
                    content={},
                ),
            )
    with database.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == status
        assert session.query(WorkOrderApproverModel).one().status == "PENDING"
        assert session.query(WorkOrderNotificationModel).count() == 0
    assert (
        SpaceRepository(database, KnowledgeSpacePersistenceEffects()).get_member(
            space_id=space_id, user_id="overflow", env="pre"
        )
        is None
    )
    assert len(history(database, space_id)) == 1


def test_two_real_connections_can_activate_same_space(database, monkeypatch):
    space_id, _ = create_space(database)
    other = CommunityDatabase(database._url)
    barrier = Barrier(2)
    lock_space = knowledge_sync.lock_space

    def racing_lock(*args, **kwargs):
        barrier.wait(timeout=5)
        return lock_space(*args, **kwargs)

    monkeypatch.setattr(knowledge_sync, "lock_space", racing_lock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    SpaceKnowledgeSyncRepository(db, enabled_capture(db)).activate,
                    space_id=space_id,
                    environment="pre",
                    app="agentclaw",
                )
                for db in (database, other)
            ]
            for future in futures:
                future.result(timeout=10)
    finally:
        other._engine.dispose()
    assert len(history(database, space_id)) == 1
    assert SpaceKnowledgeSyncRepository(database, enabled_capture(database)).progress(
        space_id=space_id, environment="pre", app="agentclaw"
    ) == (1, 0, "active")


def test_unrelated_activation_integrity_failure_is_not_swallowed(database, monkeypatch):
    space_id, _ = create_space(database)

    def fail(*args, **kwargs):
        raise IntegrityError(
            "failed insert", {}, RuntimeError("unrelated write failure")
        )

    monkeypatch.setattr(knowledge_sync, "_save_snapshot", fail)
    with pytest.raises(IntegrityError):
        SpaceKnowledgeSyncRepository(database, enabled_capture(database)).activate(
            space_id=space_id, environment="pre", app="agentclaw"
        )
    assert history(database, space_id) == []


@pytest.mark.parametrize("personal", [False, True])
def test_new_space_does_not_reuse_deleted_outbox_identity(database, personal):
    old_id, old_code = create_space(database)
    sink = BoundedSink()
    sync = synchronizer(database, sink)
    sync.ensure_synced(space_id=old_id, environment="pre")
    SpaceManagementRepository(database, effects=KnowledgeSpacePersistenceEffects()).delete_team(
        space_id=old_id, with_members=True, env="pre"
    )
    new_id, new_code = create_space(database, owner="new-owner", personal=personal)
    assert new_code != old_code
    assert new_id != old_id
    sync.ensure_synced(space_id=new_id, environment="pre")
    SpaceRepository(database, KnowledgeSpacePersistenceEffects()).add_member(
        space_id=new_id,
        user_id="new-member",
        role=SpaceRole.MEMBER,
        creator_id="new-owner",
        env="pre",
    )
    assert sync.deliver_next(space_id=old_id, environment="pre")
    assert sync.deliver_next(space_id=new_id, environment="pre")
    assert sync.progress(space_id=old_id, environment="pre") == (2, 2, "disabled")
    assert sync.progress(space_id=new_id, environment="pre") == (2, 2, "active")
    assert [p["creator_staff_no"] for p in history(database, old_id)] == [
        "owner",
        "owner",
    ]
    assert [p["creator_staff_no"] for p in history(database, new_id)] == [
        "new-owner",
        "new-owner",
    ]
    with database.orm_session() as session:
        assert session.get(SpaceModel, old_id) is None


def test_utf8_byte_limit_rolls_back_bulk_write_and_still_allows_revocation(
    database, caplog
):
    space_id, _ = create_space(database)
    # 1955 fits the byte limit; 1960 is below the count cap but exceeds 1 MiB.
    populate(database, space_id, 1955, wide=True)
    sink = BoundedSink()
    sync = synchronizer(database, sink)
    sync.ensure_synced(space_id=space_id, environment="pre")
    original = history(database, space_id)[0]
    assert len(json.dumps(original, ensure_ascii=False).encode()) < 1024 * 1024
    management = SpaceManagementRepository(database, effects=KnowledgeSpacePersistenceEffects())
    additions = {f"new{i}" + "😀" * 124: None for i in range(5)}
    with pytest.raises(SpaceKnowledgeError, match="SPACE_MEMBERSHIP_LIMIT_EXCEEDED"):
        management.add_members(
            space_id=space_id, members=additions, actor_id="owner", env="pre"
        )
    assert "payload_bytes=" in caplog.text
    assert "😀" not in caplog.text  # no rejected content / identities in diagnostics
    assert history(database, space_id) == [original]
    spaces = SpaceRepository(database, KnowledgeSpacePersistenceEffects())
    assert all(
        spaces.get_member(space_id=space_id, user_id=uid, env="pre") is None
        for uid in additions
    )
    spaces.delete_member(space_id=space_id, user_id="0000" + "😀" * 124, env="pre")
    management.delete_team(space_id=space_id, with_members=True, env="pre")
    while sync.deliver_next(space_id=space_id, environment="pre"):
        pass
    assert sync.progress(space_id=space_id, environment="pre") == (3, 3, "disabled")


def test_concurrent_new_spaces_skip_retired_scope_across_connections(database):
    old_id, _ = create_space(database)
    sync = synchronizer(database, BoundedSink())
    sync.ensure_synced(space_id=old_id, environment="pre")
    SpaceManagementRepository(database, effects=KnowledgeSpacePersistenceEffects()).delete_team(
        space_id=old_id, with_members=True, env="pre"
    )
    other = CommunityDatabase(database._url)
    barrier = Barrier(2)

    def create(db, owner, personal):
        barrier.wait(timeout=5)
        return create_space(db, owner=owner, personal=personal)[0]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(create, database, "alice", False),
                pool.submit(create, other, "bob", True),
            ]
            ids = [future.result(timeout=10) for future in futures]
    finally:
        other._engine.dispose()
    assert len({old_id, *ids}) == 3
    assert all(new_id > old_id for new_id in ids)
    assert sync.progress(space_id=old_id, environment="pre") == (2, 1, "disabled")
    for new_id in ids:
        sync.ensure_synced(space_id=new_id, environment="pre")
        assert sync.progress(space_id=new_id, environment="pre") == (1, 1, "active")


def test_new_identity_and_members_rollback_if_creation_caller_fails(database):
    old_id, _ = create_space(database)
    sync = synchronizer(database, BoundedSink())
    sync.ensure_synced(space_id=old_id, environment="pre")
    SpaceManagementRepository(database, effects=KnowledgeSpacePersistenceEffects()).delete_team(
        space_id=old_id, with_members=True, env="pre"
    )
    with pytest.raises(RuntimeError, match="creation failed"):
        with SpaceRepository(database, KnowledgeSpacePersistenceEffects()).create_team_transaction(
            name="Failed", creator_id="new-owner", creator_user_name=None, env="pre"
        ) as failed:
            assert failed.id != old_id
            raise RuntimeError("creation failed")
    with database.orm_session() as session:
        assert session.query(SpaceModel).count() == 0
        assert session.query(SpaceMemberModel).count() == 0
    assert [p["status"] for p in history(database, old_id)] == ["active", "disabled"]
    new_id, _ = create_space(database, owner="new-owner")
    assert new_id > old_id


def test_unactivated_space_membership_is_not_capped_or_activated(database):
    space_id, _ = create_space(database)
    populate(database, space_id, 2000)
    SpaceRepository(database, KnowledgeSpacePersistenceEffects()).add_member(
        space_id=space_id,
        user_id="one-more",
        role=SpaceRole.MEMBER,
        creator_id="owner",
        env="pre",
    )
    assert history(database, space_id) == []
    assert (
        SpaceRepository(database, KnowledgeSpacePersistenceEffects()).list_members(
            space_id=space_id, env="pre", keyword=None, offset=0, limit=1
        )[0]
        == 2001
    )


def test_additive_repository_creation_also_skips_retired_identity(database):
    from agentclaw.community.core.repository.implementations.skill_center.space_skill import (
        SpaceSkillRepository,
    )

    old_id, _ = create_space(database)
    sync = synchronizer(database, BoundedSink())
    sync.ensure_synced(space_id=old_id, environment="pre")
    SpaceManagementRepository(database, effects=KnowledgeSpacePersistenceEffects()).delete_team(
        space_id=old_id, with_members=True, env="pre"
    )
    created = SpaceSkillRepository(database, Mock(), effects=KnowledgeSpacePersistenceEffects()).create_space(
        {
            "space_code": "additive-code",
            "space_type": "TEAM",
            "name": "New",
            "created_by": "new-owner",
            "env": "pre",
        }
    )
    assert created["id"] != old_id
    SpaceRepository(database, KnowledgeSpacePersistenceEffects()).add_member(
        space_id=created["id"],
        user_id="new-owner",
        role=SpaceRole.ADMIN,
        creator_id="new-owner",
        env="pre",
    )
    sync.ensure_synced(space_id=created["id"], environment="pre")
    assert sync.progress(space_id=old_id, environment="pre") == (2, 1, "disabled")
    assert sync.progress(space_id=created["id"], environment="pre") == (1, 1, "active")


def test_first_activation_is_idempotent_across_real_processes(database, tmp_path):
    import subprocess
    import sys
    import textwrap

    space_id, _ = create_space(database)
    worker = textwrap.dedent("""\
        from pathlib import Path
        from tests.community.core.space_knowledge.capture_fixtures import enabled_capture
        import os
        import sys
        import time
        from agentclaw.community.core.repository.implementations.spaces import knowledge_sync
        from agentclaw.community.plugins.community.database import CommunityDatabase

        database = CommunityDatabase(sys.argv[1])
        barrier = Path(sys.argv[3])
        lock_space = knowledge_sync.lock_space
        def race(*args, **kwargs):
            (barrier / str(os.getpid())).touch()
            deadline = time.monotonic() + 10
            while len(list(barrier.iterdir())) != 2:
                if time.monotonic() >= deadline:
                    raise RuntimeError("second activation did not reach source guard")
                time.sleep(0.01)
            return lock_space(*args, **kwargs)
        knowledge_sync.lock_space = race
        try:
            knowledge_sync.SpaceKnowledgeSyncRepository(database, enabled_capture(database)).activate(
                space_id=int(sys.argv[2]), environment="pre", app="agentclaw"
            )
        finally:
            database._engine.dispose()
    """)
    barrier = tmp_path / "process-barrier"
    barrier.mkdir()
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, database._url, str(space_id), str(barrier)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stdout + stderr
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert len(list(barrier.iterdir())) == 2
    assert len(history(database, space_id)) == 1
    assert SpaceKnowledgeSyncRepository(database, enabled_capture(database)).progress(
        space_id=space_id, environment="pre", app="agentclaw"
    ) == (1, 0, "active")
