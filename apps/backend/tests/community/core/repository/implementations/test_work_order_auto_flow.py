"""Integration coverage for AUTO work-order state and business side effects."""

import asyncio
import json

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.core.models.space_skill import SkillSpaceBinding
from agentclaw.community.core.repository.implementations.skill_center.skill_editor_request import (
    SkillEditorRequestRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyProcessedError,
    WorkOrderBotEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderEventType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderModel,
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
        name="AUTO Team", creator_id="owner", creator_user_name=None, env="dev"
    ) as team:
        team.sc_team_id = "sc-auto-team"
        space_id = team.id
    for user in ("alice",):
        spaces.add_member(
            space_id=space_id,
            user_id=user,
            role=SpaceRole.MEMBER,
            creator_id="owner",
            env="dev",
        )
    with db.orm_session() as session:
        bot = BotModel(
            bot_id="auto-bot",
            bot_name="Auto Bot",
            entity_id="owner",
            entity_type="user",
            creator_id="owner",
            owner_id="owner",
            status="ACTIVE",
            bot_type="service",
            space_id=space_id,
            env="dev",
        )
        skill = Skill(
            name="auto-skill",
            git_path="center://auto-skill",
            skill_uuid="auto-skill-uuid",
            status="PUBLISHED",
            env="dev",
        )
        session.add_all((bot, skill))
        session.flush()
        bot_pk, skill_id = bot.id, skill.id
        session.add(
            SkillSpaceBinding(
                skill_id=skill_id,
                space_id=space_id,
                created_by="owner",
                env="dev",
            )
        )
    repository = WorkOrderRepository(db, SkillEditorRequestRepository(db))
    yield db, repository, space_id, bot_pk, skill_id
    reset_for_tests()


def _auto_order(repo, biz_type, biz_id, biz_data, applicant="alice"):
    return repo.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=biz_type,
        biz_id=str(biz_id),
        event_type="AUTO_TEST_APPLIED",
        applicant_user_id=applicant,
        approver_user_ids=[],
        recipient_user_ids=["notify-user"],
        title="AUTO test",
        content=None,
        apply_reason="automatic request",
        biz_data=json.dumps(biz_data),
        env="dev",
    )


def test_auto_space_join_commits_membership_status_and_notice_together(setup):
    db, repo, space_id, _, _ = setup
    result = _auto_order(
        repo, WorkOrderBizType.SPACE_JOIN.value, space_id, {}, applicant="bob"
    )

    notice_ids = repo.complete_auto_approval(
        work_order_id=result.work_order_id,
        recipient_user_ids=["bob"],
        source_event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
        env="dev",
    )

    with db.orm_session() as session:
        order = session.get(WorkOrderModel, result.work_order_id)
        member = (
            session.query(SpaceMemberModel)
            .filter_by(space_id=space_id, user_id="bob", env="dev")
            .one()
        )
        assert order.status == WorkOrderStatus.APPROVED.value
        assert order.reviewer_user_id == "SYSTEM"
        assert member.created_by == "SYSTEM"
        assert session.query(WorkOrderApproverModel).count() == 0
        assert (
            session.get(WorkOrderNotificationModel, notice_ids[0]).recipient_user_id
            == "bob"
        )


def test_auto_space_join_notice_failure_rolls_back_membership_and_approval(setup):
    db, repo, space_id, _, _ = setup
    result = _auto_order(
        repo, WorkOrderBizType.SPACE_JOIN.value, space_id, {}, applicant="bob"
    )

    def reject_notice(session, _flush_context, _instances):
        if any(isinstance(row, WorkOrderNotificationModel) for row in session.new):
            raise RuntimeError("notice persistence failed")

    event.listen(Session, "before_flush", reject_notice)
    try:
        with pytest.raises(RuntimeError, match="notice persistence failed"):
            repo.complete_auto_approval(
                work_order_id=result.work_order_id,
                recipient_user_ids=["bob"],
                source_event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
                env="dev",
            )
    finally:
        event.remove(Session, "before_flush", reject_notice)

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, result.work_order_id).status == "PROCESSING"
        assert (
            session.query(SpaceMemberModel)
            .filter_by(space_id=space_id, user_id="bob", env="dev")
            .count()
            == 0
        )
        assert session.query(WorkOrderNotificationModel).count() == 0


def test_auto_bot_completion_requires_policy_and_commits_result(setup):
    db, repo, space_id, bot_pk, _ = setup
    with db.orm_session() as session:
        session.get(BotModel, bot_pk).ext = json.dumps(
            {"editor_request_auto_approve": True}
        )
    result = _auto_order(
        repo,
        WorkOrderBizType.BOT_COLLABORATOR.value,
        "auto-bot",
        {
            "bot_pk": bot_pk,
            "bot_id": "auto-bot",
            "owner_id": "owner",
            "space_id": space_id,
        },
    )

    notice_ids = repo.complete_auto_approval(
        work_order_id=result.work_order_id,
        recipient_user_ids=["alice"],
        source_event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
        env="dev",
    )

    with db.orm_session() as session:
        order = session.get(WorkOrderModel, result.work_order_id)
        collaborator = (
            session.query(BotCollaboratorModel)
            .filter_by(bot_pk=bot_pk, user_id="alice", env="dev")
            .one()
        )
        assert order.status == WorkOrderStatus.APPROVED.value
        assert collaborator.operator_id == "SYSTEM"
        assert (
            session.get(WorkOrderNotificationModel, notice_ids[0]).recipient_user_id
            == "alice"
        )


def test_auto_bot_notice_failure_rolls_back_collaborator_grant(setup):
    db, repo, space_id, bot_pk, _ = setup
    with db.orm_session() as session:
        session.get(BotModel, bot_pk).ext = json.dumps(
            {"editor_request_auto_approve": True}
        )
    result = _auto_order(
        repo,
        WorkOrderBizType.BOT_COLLABORATOR.value,
        "auto-bot",
        {
            "bot_pk": bot_pk,
            "bot_id": "auto-bot",
            "owner_id": "owner",
            "space_id": space_id,
        },
    )

    def reject_notice(session, _flush_context, _instances):
        if any(isinstance(row, WorkOrderNotificationModel) for row in session.new):
            raise RuntimeError("notice persistence failed")

    event.listen(Session, "before_flush", reject_notice)
    try:
        with pytest.raises(RuntimeError, match="notice persistence failed"):
            repo.complete_auto_approval(
                work_order_id=result.work_order_id,
                recipient_user_ids=["alice"],
                source_event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
                env="dev",
            )
    finally:
        event.remove(Session, "before_flush", reject_notice)

    with db.orm_session() as session:
        assert session.get(WorkOrderModel, result.work_order_id).status == "PROCESSING"
        assert session.query(BotCollaboratorModel).count() == 0
        assert session.query(WorkOrderNotificationModel).count() == 0


def test_auto_bot_completion_rejects_disabled_owner_policy(setup):
    db, repo, space_id, bot_pk, _ = setup
    result = _auto_order(
        repo,
        WorkOrderBizType.BOT_COLLABORATOR.value,
        "auto-bot",
        {
            "bot_pk": bot_pk,
            "bot_id": "auto-bot",
            "owner_id": "owner",
            "space_id": space_id,
        },
    )

    with pytest.raises(WorkOrderBotEditorRequestNotAllowedError, match="disabled"):
        repo.complete_auto_approval(
            work_order_id=result.work_order_id,
            recipient_user_ids=["alice"],
            source_event_type=WorkOrderEventType.BOT_COLLABORATOR_APPLIED.value,
            env="dev",
        )
    with db.orm_session() as session:
        assert session.get(WorkOrderModel, result.work_order_id).status == "PROCESSING"
        assert session.query(BotCollaboratorModel).count() == 0
        assert session.query(WorkOrderNotificationModel).count() == 0


def test_auto_failure_commits_terminal_state_and_notice_together(setup):
    db, repo, _, _, _ = setup
    result = _auto_order(repo, WorkOrderBizType.BOT_FRIEND.value, "friend-1", {})

    notice_ids = repo.fail_auto_approval(
        work_order_id=result.work_order_id,
        recipient_user_ids=["alice"],
        source_event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
        review_remark="business callback failed",
        env="dev",
    )

    with db.orm_session() as session:
        order = session.get(WorkOrderModel, result.work_order_id)
        assert order.status == WorkOrderStatus.FAILED.value
        assert order.reviewer_user_id == "SYSTEM"
        assert order.review_remark == "business callback failed"
        assert (
            session.get(WorkOrderNotificationModel, notice_ids[0]).recipient_user_id
            == "alice"
        )
    with pytest.raises(WorkOrderAlreadyProcessedError):
        repo.complete_auto_approval(
            work_order_id=result.work_order_id,
            recipient_user_ids=["alice"],
            source_event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
            env="dev",
        )


def test_auto_context_requires_system_and_processing_state(setup):
    _, repo, _, _, _ = setup
    result = _auto_order(repo, WorkOrderBizType.BOT_FRIEND.value, "friend-2", {})

    context = repo.get_approval_context(
        work_order_id=result.work_order_id, reviewer_user_id="SYSTEM", env="dev"
    )

    assert context.approver is None
    assert context.work_order.status is WorkOrderStatus.PROCESSING
    with pytest.raises(WorkOrderAccessDeniedError):
        repo.get_approval_context(
            work_order_id=result.work_order_id, reviewer_user_id="alice", env="dev"
        )


def test_auto_result_notice_is_deduplicated_per_recipient(setup):
    db, repo, _, _, _ = setup
    result = _auto_order(repo, WorkOrderBizType.BOT_FRIEND.value, "friend-3", {})

    notice_ids = repo.complete_auto_approval(
        work_order_id=result.work_order_id,
        recipient_user_ids=["alice", "alice", "bob"],
        source_event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
        env="dev",
    )

    with db.orm_session() as session:
        notices = (
            session.query(WorkOrderNotificationModel)
            .order_by(WorkOrderNotificationModel.recipient_user_id)
            .all()
        )
        assert [notice.recipient_user_id for notice in notices] == ["alice", "bob"]
        assert len(notice_ids) == 2
        assert all(notice.notification_category == "NOTICE" for notice in notices)
        assert all("自动审批已通过" in notice.content for notice in notices)
