"""Policy and atomic auto-approval exercised against the real SQLite repository."""

import asyncio
import json

import pytest
from sqlalchemy import event

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_editor_request import (
    SkillEditorRequestRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderApplicantAlreadyEditorError,
    WorkOrderBotEditorRequestNotAllowedError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import WorkOrderStatus
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderApproverModel,
    WorkOrderNotificationModel,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests


@pytest.fixture
def setup():
    reset_for_tests()
    db = SqliteDB()
    asyncio.run(db.bootstrap())
    spaces = SpaceRepository(db)
    with spaces.create_team_transaction(
        name="Team", creator_id="owner", creator_user_name=None, env="dev"
    ) as team:
        team.sc_team_id = "sc-team"
        space_id = team.id
    for user in ("alice", "bob"):
        spaces.add_member(
            space_id=space_id,
            user_id=user,
            role=SpaceRole.MEMBER,
            creator_id="owner",
            env="dev",
        )
    with db.orm_session() as session:
        bot = BotModel(
            bot_id="bot-1",
            bot_name="Test Bot",
            entity_id="owner",
            entity_type="user",
            creator_id="owner",
            owner_id="owner",
            status="ACTIVE",
            bot_type="service",
            space_id=space_id,
            env="dev",
            ext=json.dumps({"keep": {"value": 1}}),
        )
        session.add(bot)
        session.flush()
        bot_pk = bot.id
    repo = WorkOrderRepository(db, SkillEditorRequestRepository(db))
    request = dict(
        bot_pk=bot_pk,
        bot_id="bot-1",
        bot_name="Test Bot",
        owner_id="owner",
        space_id=space_id,
        applicant_user_id="alice",
        applicant_name="Alice",
        apply_reason="edit",
        env="dev",
    )
    policy = dict(bot_id="bot-1", owner_id="owner", actor_id="owner", env="dev")
    yield db, repo, request, policy
    reset_for_tests()


def test_policy_default_and_merge(setup):
    db, repo, _, policy = setup
    assert repo.get_bot_editor_request_policy(**policy) is False
    assert repo.update_bot_editor_request_policy(**policy, auto_approve=True) is True
    assert repo.get_bot_editor_request_policy(**policy) is True
    with db.orm_session() as session:
        assert json.loads(session.query(BotModel).one().ext) == {
            "keep": {"value": 1},
            "editor_request_auto_approve": True,
        }


@pytest.mark.parametrize(
    "method", ["get_bot_editor_request_policy", "update_bot_editor_request_policy"]
)
@pytest.mark.parametrize("actor", ["alice", "bob", "stranger"])
def test_policy_owner_only(setup, method, actor):
    _, repo, _, policy = setup
    policy["actor_id"] = actor
    if method.startswith("update"):
        policy["auto_approve"] = True
    with pytest.raises(WorkOrderAccessDeniedError):
        getattr(repo, method)(**policy)


@pytest.mark.parametrize(
    "change",
    [
        {"bot_id": "missing"},
        {"env": "prod"},
        {"owner_id": "stranger", "actor_id": "stranger"},
    ],
)
def test_policy_scopes_bot_identity(setup, change):
    _, repo, _, policy = setup
    with pytest.raises(WorkOrderNotFoundError):
        repo.update_bot_editor_request_policy(**(policy | change), auto_approve=True)


@pytest.mark.parametrize("change", ["personal", "deleted", "missing"])
def test_policy_requires_available_team_space(setup, change):
    db, repo, request, policy = setup
    with db.orm_session() as session:
        space = session.get(SpaceModel, request["space_id"])
        if change == "personal":
            space.space_type = "PERSONAL"
        elif change == "deleted":
            from datetime import datetime

            space.deleted_at = datetime.now()
        else:
            session.query(BotModel).one().space_id = None
    with pytest.raises(WorkOrderBotEditorRequestNotAllowedError):
        repo.update_bot_editor_request_policy(**policy, auto_approve=True)


def test_auto_approval_atomically_grants_member_and_notifies_applicant(setup):
    db, repo, request, policy = setup
    repo.update_bot_editor_request_policy(**policy, auto_approve=True)
    result = repo.create_bot_editor_request(**request)
    assert result.status is WorkOrderStatus.APPROVED
    assert result.reviewer_user_id is None
    assert result.reviewed_at is not None
    assert json.loads(result.biz_data)["approval_mode"] == "auto"
    assert (
        repo.get_detail(
            work_order_id=result.id, actor_id="alice", env="dev"
        ).can_approve
        is False
    )
    with db.orm_session() as session:
        relation = session.query(BotCollaboratorModel).one()
        assert (relation.user_id, relation.role, relation.operator_id) == (
            "alice",
            "member",
            "owner",
        )
        assert session.query(WorkOrderApproverModel).count() == 0
        notice = session.query(WorkOrderNotificationModel).one()
        assert (notice.recipient_user_id, notice.notification_category) == (
            "alice",
            "NOTICE",
        )
        assert json.loads(notice.content)["approval_mode"] == "auto"
    with pytest.raises(WorkOrderApplicantAlreadyEditorError):
        repo.create_bot_editor_request(**request)


def test_policy_changes_only_affect_new_applications(setup):
    db, repo, request, policy = setup
    pending = repo.create_bot_editor_request(**request)
    repo.update_bot_editor_request_policy(**policy, auto_approve=True)
    with pytest.raises(WorkOrderAlreadyPendingError):
        repo.create_bot_editor_request(**request)
    approved = repo.create_bot_editor_request(
        **(request | {"applicant_user_id": "bob"})
    )
    repo.update_bot_editor_request_policy(**policy, auto_approve=False)
    with db.orm_session() as session:
        assert session.get(WorkOrderModel, pending.id).status == "PENDING"
        assert session.get(WorkOrderModel, approved.id).status == "APPROVED"
        assert session.query(BotCollaboratorModel).one().user_id == "bob"
        assert session.query(WorkOrderApproverModel).one().status == "PENDING"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "{}",
        "null",
        "[]",
        "bad-json",
        '{"editor_request_auto_approve":"true"}',
        '{"editor_request_auto_approve":1}',
    ],
)
def test_only_explicit_boolean_true_enables_auto_approval(setup, raw):
    db, repo, request, policy = setup
    with db.orm_session() as session:
        session.query(BotModel).one().ext = raw
    assert repo.get_bot_editor_request_policy(**policy) is False
    assert repo.create_bot_editor_request(**request).status is WorkOrderStatus.PENDING


def test_auto_approval_still_requires_current_membership(setup):
    db, repo, request, policy = setup
    repo.update_bot_editor_request_policy(**policy, auto_approve=True)
    with db.orm_session() as session:
        session.query(SpaceMemberModel).filter_by(user_id="alice").delete()
    with pytest.raises(WorkOrderAccessDeniedError):
        repo.create_bot_editor_request(**request)
    with db.orm_session() as session:
        assert session.query(WorkOrderModel).count() == 0
        assert session.query(BotCollaboratorModel).count() == 0


def test_failed_notification_rolls_back_order_and_grant(setup):
    db, repo, request, policy = setup
    repo.update_bot_editor_request_policy(**policy, auto_approve=True)

    def fail(*args):
        raise RuntimeError("notification write failed")

    event.listen(WorkOrderNotificationModel, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="notification write failed"):
            repo.create_bot_editor_request(**request)
    finally:
        event.remove(WorkOrderNotificationModel, "before_insert", fail)
    with db.orm_session() as session:
        assert session.query(WorkOrderModel).count() == 0
        assert session.query(BotCollaboratorModel).count() == 0
