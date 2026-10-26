"""Legacy business commands must persist without provisioning knowledge schema."""

import pytest
from sqlalchemy import event

from agentclaw.community.core.repository.protocols.spaces import (
    SpaceRepositoryProtocol,
    SpaceManagementRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SpaceSkillRepository,
)
from agentclaw.community.core.repository.protocols.work_orders import (
    WorkOrderRepositoryProtocol,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceModel,
    SpaceMemberModel,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderDecision,
    WorkOrderStatus,
    WorkOrderNotificationDraft,
    NotificationCategory,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderApproverModel,
)
from tests.community.core.space_knowledge.capture_fixtures import (
    missing_knowledge_graph as missing_knowledge_graph,
)


@pytest.mark.parametrize(
    "operation",
    [
        "team",
        "personal",
        "skill_space",
        "add",
        "remove",
        "role",
        "batch_add",
        "batch_remove",
        "batch_role",
        "rename",
        "delete",
        "manual",
        "legacy_approval",
        "auto",
    ],
)
def test_default_graph_legacy_commands_persist_without_knowledge_table(
    missing_knowledge_graph,
    operation,
):
    graph, db = missing_knowledge_graph
    spaces = graph.get(SpaceRepositoryProtocol)
    management = graph.get(SpaceManagementRepositoryProtocol)
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(db._engine, "before_cursor_execute", capture)
    try:
        if operation == "team":
            with spaces.create_team_transaction(
                name="New", creator_id="bob", creator_user_name=None, env="pre"
            ) as created:
                result_id = created.id
        elif operation == "personal":
            with spaces.create_personal_transaction(
                user_id="bob", creator_user_name=None, env="pre"
            ) as created:
                result_id = created.id
        elif operation == "skill_space":
            result_id = graph.get(SpaceSkillRepository).create_space(
                {
                    "space_code": "new",
                    "space_type": "TEAM",
                    "name": "New",
                    "created_by": "bob",
                    "env": "pre",
                }
            )["id"]
        elif operation == "add":
            spaces.add_member(
                space_id=1,
                user_id="bob",
                role=SpaceRole.MEMBER,
                creator_id="owner",
                env="pre",
            )
        elif operation == "remove":
            assert spaces.delete_member(space_id=1, user_id="alice", env="pre")
        elif operation == "role":
            spaces.update_member_role(
                space_id=1, user_id="alice", role=SpaceRole.ADMIN, env="pre"
            )
        elif operation == "batch_add":
            management.add_members(
                space_id=1, members={"bob": None}, actor_id="owner", env="pre"
            )
        elif operation == "batch_remove":
            management.remove_members(space_id=1, user_ids=["alice"], env="pre")
        elif operation == "batch_role":
            management.set_member_roles(
                space_id=1, user_ids=["alice"], role="ADMIN", env="pre"
            )
        elif operation == "rename":
            management.rename_team(
                space_id=1, name="Renamed", actor_id="owner", env="pre"
            )
        elif operation == "delete":
            management.delete_team(space_id=1, with_members=True, env="pre")
        else:
            with db.orm_session() as session:
                order = WorkOrderModel(
                    work_order_no="WO-legacy",
                    biz_type="SPACE_JOIN",
                    biz_id="1",
                    applicant_user_id="bob",
                    env="pre",
                    status="PROCESSING" if operation == "auto" else "PENDING",
                    approval_mode="AUTO" if operation == "auto" else "MANUAL",
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
            orders = graph.get(WorkOrderRepositoryProtocol)
            if operation == "manual":
                orders.process_approval(
                    work_order_id=order_id,
                    reviewer_user_id="owner",
                    decision=WorkOrderDecision.APPROVED,
                    review_remark=None,
                    env="pre",
                )
            elif operation == "legacy_approval":
                orders.review_space_join(
                    work_order_id=order_id,
                    reviewer_user_id="owner",
                    target_status=WorkOrderStatus.APPROVED,
                    review_remark=None,
                    applicant_user_name="Bob",
                    notification=WorkOrderNotificationDraft(
                        recipient_user_id="bob",
                        notification_category=NotificationCategory.NOTICE,
                        event_type="SPACE_JOIN_REVIEWED",
                        biz_type="SPACE_JOIN",
                        biz_id="1",
                        title="Approved",
                        content={},
                    ),
                    env="pre",
                )
            else:
                orders.complete_auto_approval(
                    work_order_id=order_id,
                    recipient_user_ids=["bob"],
                    source_event_type="SPACE_JOIN_REQUESTED",
                    env="pre",
                )
    finally:
        event.remove(db._engine, "before_cursor_execute", capture)

    with db.orm_session() as session:
        if operation in {"team", "personal", "skill_space"}:
            assert session.get(SpaceModel, result_id).created_by == "bob"
        elif operation in {"add", "batch_add", "manual", "legacy_approval", "auto"}:
            assert (
                session.query(SpaceMemberModel)
                .filter_by(space_id=1, user_id="bob")
                .one()
                .role
                == "MEMBER"
            )
        elif operation in {"remove", "batch_remove"}:
            assert (
                session.query(SpaceMemberModel)
                .filter_by(space_id=1, user_id="alice")
                .count()
                == 0
            )
        elif operation in {"role", "batch_role"}:
            assert (
                session.query(SpaceMemberModel)
                .filter_by(space_id=1, user_id="alice")
                .one()
                .role
                == "ADMIN"
            )
        elif operation == "rename":
            assert session.get(SpaceModel, 1).name == "Renamed"
        else:
            assert session.get(SpaceModel, 1) is None
    assert not any("FROM ac_space_knowledge_sync" in sql for sql in statements)
    assert not any("UPDATE ac_space SET id=ac_space.id" in sql for sql in statements)


@pytest.mark.parametrize("personal", [False, True])
def test_no_knowledge_pilot_creates_and_mutates_without_outbox(
    missing_knowledge_graph, personal
):
    from agentclaw.community.core.repository.implementations.spaces.space import (
        SpaceRepository,
    )
    from agentclaw.community.core.repository.implementations.spaces.persistence_effects import (
        NoKnowledgeSpacePersistenceEffects,
    )

    _, db = missing_knowledge_graph
    spaces = SpaceRepository(db, NoKnowledgeSpacePersistenceEffects())
    transaction = (
        spaces.create_personal_transaction(
            user_id="bob", creator_user_name=None, env="pre"
        )
        if personal
        else spaces.create_team_transaction(
            name="Pilot", creator_id="bob", creator_user_name=None, env="pre"
        )
    )
    with transaction as created:
        sid = created.id
    spaces.add_member(
        space_id=sid,
        user_id="carol",
        role=SpaceRole.MEMBER,
        creator_id="bob",
        env="pre",
    )
    spaces.update_member_role(
        space_id=sid, user_id="carol", role=SpaceRole.ADMIN, env="pre"
    )
    assert (
        spaces.get_member(space_id=sid, user_id="carol", env="pre").role
        == SpaceRole.ADMIN
    )
    assert spaces.delete_member(space_id=sid, user_id="carol", env="pre")
    assert spaces.get_member(space_id=sid, user_id="carol", env="pre") is None


def test_default_full_graph_shares_one_guard_and_constructs_without_db_io(
    missing_knowledge_graph,
):
    from agentclaw.community.core.repository.protocols.space_persistence_effects import (
        SpacePersistenceEffects,
    )
    from agentclaw.community.core.repository.protocols.spaces import (
        SpaceKnowledgeSyncRepositoryProtocol,
    )
    from agentclaw.community.core.space_knowledge.capture_guard import (
        SpaceKnowledgeCaptureGuard,
    )
    from agentclaw.community.core.space_knowledge.capture_lifecycle import (
        SpaceKnowledgeCaptureLifecycle,
    )

    graph, db = missing_knowledge_graph
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(db._engine, "before_cursor_execute", capture)
    try:
        lifecycle = graph.get(SpaceKnowledgeCaptureLifecycle)
        assert graph.get(SpacePersistenceEffects) is lifecycle
        assert graph.get(SpaceKnowledgeCaptureGuard) is lifecycle
        for contract in (
            SpaceRepositoryProtocol, SpaceManagementRepositoryProtocol,
            WorkOrderRepositoryProtocol, SpaceSkillRepository,
        ):
            assert graph.get(contract)._effects is lifecycle
        assert graph.get(SpaceKnowledgeSyncRepositoryProtocol)._capture is lifecycle
        assert statements == []
    finally:
        event.remove(db._engine, "before_cursor_execute", capture)
