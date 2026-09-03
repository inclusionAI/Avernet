"""SQLite-backed unit tests for the new unified ORM repositories."""

import asyncio
import json

import pytest

from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.market_favorites.models import (
    FavoriteTargetType,
    MarketSource,
)
from agentclaw.community.core.repository.implementations.market_favorites.favorite import (
    MarketFavoriteRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.space_skill import (
    SpaceSkillRepository,
)
from agentclaw.community.core.repository.implementations.skill_center.skill_editor_request import (
    SkillEditorRequestRepository,
)
from agentclaw.community.core.models.space_skill import SkillGrant
from agentclaw.community.core.spaces.models import SpaceJoinStatus, SpaceRole, SpaceType
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderNoReviewerError,
    WorkOrderNotFoundError,
    WorkOrderSkillEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderApproverStatus,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderNotificationDraft,
    WorkOrderQueryType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderNotificationModel,
    WorkOrderModel,
)
from agentclaw.community.plugin_api.models import BotModel
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests


@pytest.fixture
def db():
    reset_for_tests()
    plugin = SqliteDB()
    asyncio.run(plugin.bootstrap())
    yield plugin
    reset_for_tests()


def _team(spaces: SpaceRepository, name="Team", creator="owner-1"):
    with spaces.create_team_transaction(
        name=name, creator_id=creator, creator_user_name=None, env="dev"
    ) as row:
        row.sc_team_id = f"sc-{name}-{creator}"
        return row


def _skill_editor_requests(db) -> SkillEditorRequestRepository:
    return SkillEditorRequestRepository(db)


def _work_orders(db) -> WorkOrderRepository:
    return WorkOrderRepository(db, _skill_editor_requests(db))


def _space_skills(db) -> SpaceSkillRepository:
    return SpaceSkillRepository(db, _skill_editor_requests(db))


def _review_notification(
    *, applicant_user_id: str, space_id: int, title: str, content: str
) -> WorkOrderNotificationDraft:
    return WorkOrderNotificationDraft(
        recipient_user_id=applicant_user_id,
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.SPACE_JOIN_REVIEWED,
        biz_type=WorkOrderBizType.SPACE_JOIN,
        biz_id=str(space_id),
        title=title,
        content=content,
    )


def _bot_review_notification(
    *, applicant_user_id: str, bot_id: str
) -> WorkOrderNotificationDraft:
    return WorkOrderNotificationDraft(
        recipient_user_id=applicant_user_id,
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.BOT_COLLABORATOR_REVIEWED,
        biz_type=WorkOrderBizType.BOT_COLLABORATOR,
        biz_id=bot_id,
        title="Bot 共同编辑申请已通过",
        content="approved",
    )


def _skill_review_notification(
    *, applicant_user_id: str, skill_id: int, approved: bool
) -> WorkOrderNotificationDraft:
    return WorkOrderNotificationDraft(
        recipient_user_id=applicant_user_id,
        notification_category=NotificationCategory.NOTICE,
        event_type=WorkOrderEventType.SKILL_COLLABORATOR_REVIEWED,
        biz_type=WorkOrderBizType.SKILL_COLLABORATOR,
        biz_id=str(skill_id),
        title="Skill 共同编辑申请已通过" if approved else "Skill 共同编辑申请未通过",
        content="reviewed",
    )


def _space_skill(db, spaces: SpaceRepository):
    team = _team(spaces)
    spaces.add_member(
        space_id=team.id,
        user_id="applicant-1",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    created = _space_skills(db).create_space_skill(
        skill_data={
            "name": "review-skill",
            "description": None,
            "env": "dev",
            "skill_uuid": "11111111-1111-4111-8111-111111111111",
            "zip_url": (
                "draft://11111111-1111-4111-8111-111111111111/"
                "v1/22222222-2222-4222-8222-222222222222"
            ),
            "draft_target_version": 1,
            "draft_status": "EDITING",
            "draft_description": "Review skill",
            "draft_source_kind": "FOLDER",
            "creation_request_id": "review-skill-create",
            "creation_request_hash": "a" * 64,
            "source_type": "FOLDER",
        },
        ownership_data={
            "space_id": team.id,
            "created_by": "owner-1",
            "env": "dev",
        },
        owner_grant_data={
            "user_id": "owner-1",
            "role": "OWNER",
            "granted_by": "owner-1",
            "env": "dev",
        },
    )
    return team, created["skill"]["id"]


@pytest.mark.parametrize(
    ("target_status", "manager_expected"),
    [(WorkOrderStatus.APPROVED, True), (WorkOrderStatus.REJECTED, False)],
)
def test_skill_editor_review_atomically_controls_manager_grant(
    db, target_status, manager_expected
) -> None:
    spaces = SpaceRepository(db)
    team, skill_id = _space_skill(db, spaces)
    repository = _work_orders(db)
    order = repository.create_skill_editor_request(
        space_id=team.id,
        skill_id=skill_id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="maintain together",
        env="dev",
    )

    with pytest.raises(WorkOrderAlreadyPendingError):
        repository.create_skill_editor_request(
            space_id=team.id,
            skill_id=skill_id,
            applicant_user_id="applicant-1",
            applicant_name="Applicant",
            apply_reason="duplicate",
            env="dev",
        )

    result = repository.review_skill_editor_request(
        work_order_id=order.id,
        reviewer_user_id="owner-1",
        review_remark=None if manager_expected else "not now",
        target_status=target_status,
        notification=_skill_review_notification(
            applicant_user_id="applicant-1",
            skill_id=skill_id,
            approved=manager_expected,
        ),
        env="dev",
    )

    assert result.status is target_status
    with db.orm_session() as session:
        manager = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
                SkillGrant.status == "ACTIVE",
                SkillGrant.env == "dev",
            )
            .one_or_none()
        )
    assert (manager is not None) is manager_expected


def test_skill_editor_request_rejects_personal_space(db) -> None:
    spaces = SpaceRepository(db)
    with spaces.create_personal_transaction(
        user_id="owner-1", creator_user_name=None, env="dev"
    ) as personal:
        created = _space_skills(db).create_space_skill(
            skill_data={
                "name": "personal-skill",
                "description": None,
                "env": "dev",
                "skill_uuid": "33333333-3333-4333-8333-333333333333",
                "zip_url": (
                    "draft://33333333-3333-4333-8333-333333333333/"
                    "v1/44444444-4444-4444-8444-444444444444"
                ),
                "draft_target_version": 1,
                "draft_status": "EDITING",
                "draft_description": "Personal skill",
                "draft_source_kind": "FOLDER",
                "creation_request_id": "personal-skill-create",
                "creation_request_hash": "b" * 64,
                "source_type": "FOLDER",
            },
            ownership_data={
                "space_id": personal.id,
                "created_by": "owner-1",
                "env": "dev",
            },
            owner_grant_data={
                "user_id": "owner-1",
                "role": "OWNER",
                "granted_by": "owner-1",
                "env": "dev",
            },
        )

    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError):
        _work_orders(db).create_skill_editor_request(
            space_id=personal.id,
            skill_id=created["skill"]["id"],
            applicant_user_id="owner-1",
            applicant_name="Owner",
            apply_reason="not supported",
            env="dev",
        )


def test_skill_editor_request_rejects_non_member(db) -> None:
    spaces = SpaceRepository(db)
    team, skill_id = _space_skill(db, spaces)

    with pytest.raises(WorkOrderAccessDeniedError):
        _work_orders(db).create_skill_editor_request(
            space_id=team.id,
            skill_id=skill_id,
            applicant_user_id="outsider-1",
            applicant_name="Outsider",
            apply_reason="not a member",
            env="dev",
        )


def test_skill_editor_approval_is_idempotent_when_manager_grant_already_exists(
    db,
) -> None:
    spaces = SpaceRepository(db)
    team, skill_id = _space_skill(db, spaces)
    work_orders = _work_orders(db)
    order = work_orders.create_skill_editor_request(
        space_id=team.id,
        skill_id=skill_id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="maintain together",
        env="dev",
    )
    _space_skills(db).add_manager(
        space_id=team.id,
        skill_id=skill_id,
        actor_id="owner-1",
        manager_user_id="applicant-1",
        env="dev",
    )

    result = work_orders.review_skill_editor_request(
        work_order_id=order.id,
        reviewer_user_id="owner-1",
        review_remark=None,
        target_status=WorkOrderStatus.APPROVED,
        notification=_skill_review_notification(
            applicant_user_id="applicant-1", skill_id=skill_id, approved=True
        ),
        env="dev",
    )

    assert result.status is WorkOrderStatus.APPROVED
    with db.orm_session() as session:
        managers = (
            session.query(SkillGrant)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
                SkillGrant.role == "MANAGER",
                SkillGrant.status == "ACTIVE",
                SkillGrant.env == "dev",
            )
            .all()
        )
    assert len(managers) == 1


def test_skill_editor_pending_reviewer_follows_current_owner(db) -> None:
    spaces = SpaceRepository(db)
    team, skill_id = _space_skill(db, spaces)
    spaces.add_member(
        space_id=team.id,
        user_id="owner-2",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    work_orders = _work_orders(db)
    order = work_orders.create_skill_editor_request(
        space_id=team.id,
        skill_id=skill_id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="maintain together",
        env="dev",
    )

    _space_skills(db).transfer_owner(
        space_id=team.id,
        skill_id=skill_id,
        actor_id="owner-1",
        new_owner_user_id="owner-2",
        reason=None,
        env="dev",
    )

    with pytest.raises(WorkOrderAccessDeniedError):
        work_orders.review_skill_editor_request(
            work_order_id=order.id,
            reviewer_user_id="owner-1",
            review_remark=None,
            target_status=WorkOrderStatus.APPROVED,
            notification=_skill_review_notification(
                applicant_user_id="applicant-1", skill_id=skill_id, approved=True
            ),
            env="dev",
        )

    result = work_orders.review_skill_editor_request(
        work_order_id=order.id,
        reviewer_user_id="owner-2",
        review_remark=None,
        target_status=WorkOrderStatus.APPROVED,
        notification=_skill_review_notification(
            applicant_user_id="applicant-1", skill_id=skill_id, approved=True
        ),
        env="dev",
    )
    assert result.status is WorkOrderStatus.APPROVED


def test_skill_editor_approval_rechecks_active_membership_and_rolls_back(db) -> None:
    spaces = SpaceRepository(db)
    team, skill_id = _space_skill(db, spaces)
    work_orders = _work_orders(db)
    order = work_orders.create_skill_editor_request(
        space_id=team.id,
        skill_id=skill_id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="maintain together",
        env="dev",
    )
    with db.orm_session() as session:
        member = (
            session.query(SpaceMemberModel)
            .filter(
                SpaceMemberModel.space_id == team.id,
                SpaceMemberModel.user_id == "applicant-1",
                SpaceMemberModel.env == "dev",
            )
            .one()
        )
        member.status = "INACTIVE"

    with pytest.raises(WorkOrderSkillEditorRequestNotAllowedError):
        work_orders.review_skill_editor_request(
            work_order_id=order.id,
            reviewer_user_id="owner-1",
            review_remark=None,
            target_status=WorkOrderStatus.APPROVED,
            notification=_skill_review_notification(
                applicant_user_id="applicant-1", skill_id=skill_id, approved=True
            ),
            env="dev",
        )

    with db.orm_session() as session:
        persisted_status = session.get(WorkOrderModel, order.id).status
        manager = (
            session.query(SkillGrant.id)
            .filter(
                SkillGrant.skill_id == skill_id,
                SkillGrant.user_id == "applicant-1",
                SkillGrant.status == "ACTIVE",
                SkillGrant.env == "dev",
            )
            .one_or_none()
        )
    assert persisted_status == WorkOrderStatus.PENDING.value
    assert manager is None


def test_bot_editor_request_approval_creates_member_collaborator(db) -> None:
    spaces = SpaceRepository(db)
    team = _team(spaces)
    spaces.add_member(
        space_id=team.id,
        user_id="applicant-1",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    with db.orm_session() as session:
        bot = BotModel(
            bot_id="bot-editor-1",
            bot_name="Editor Bot",
            entity_id="owner-1",
            entity_type="user",
            creator_id="owner-1",
            owner_id="owner-1",
            status="ACTIVE",
            bot_type="service",
            space_id=team.id,
            env="dev",
        )
        session.add(bot)
        session.flush()
        session.refresh(bot)
        bot_pk = bot.id

    repository = _work_orders(db)
    record = repository.create_bot_editor_request(
        bot_pk=bot_pk,
        bot_id="bot-editor-1",
        bot_name="Editor Bot",
        owner_id="owner-1",
        space_id=team.id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="joint editing",
        env="dev",
    )
    data = json.loads(record.biz_data)
    assert data["requested_role"] == "member"
    assert data["space_id"] == team.id

    with pytest.raises(WorkOrderAlreadyPendingError):
        repository.create_bot_editor_request(
            bot_pk=bot_pk,
            bot_id="bot-editor-1",
            bot_name="Editor Bot",
            owner_id="owner-1",
            space_id=team.id,
            applicant_user_id="applicant-1",
            applicant_name="Applicant",
            apply_reason="again",
            env="dev",
        )

    result = repository.review_bot_editor_request(
        work_order_id=record.id,
        reviewer_user_id="owner-1",
        review_remark=None,
        target_status=WorkOrderStatus.APPROVED,
        notification=_bot_review_notification(
            applicant_user_id="applicant-1", bot_id="bot-editor-1"
        ),
        env="dev",
    )
    assert result.status is WorkOrderStatus.APPROVED
    with db.orm_session() as session:
        collaborator = (
            session.query(BotCollaboratorModel)
            .filter(
                BotCollaboratorModel.bot_pk == bot_pk,
                BotCollaboratorModel.user_id == "applicant-1",
                BotCollaboratorModel.env == "dev",
            )
            .one()
        )
        assert collaborator.role == "member"
        assert collaborator.operator_id == "owner-1"

    total, items = repository.list_items(
        actor_id="applicant-1",
        env="dev",
        query_type=WorkOrderQueryType.INITIATED_BY_ME,
        item_type=WorkOrderItemType.ALL,
        biz_type=WorkOrderBizType.BOT_COLLABORATOR.value,
        biz_id="bot-editor-1",
        offset=0,
        limit=20,
    )
    assert total == 1
    assert items[0].work_order.id == record.id


def test_unified_work_order_create_and_approval_lifecycle(db) -> None:
    repository = _work_orders(db)

    record = repository.create_work_order(
        biz_type="BOT_COLLABORATOR",
        biz_id="bot-42",
        applicant_user_id="applicant-42",
        apply_reason="request collaboration",
        biz_data='{"source": "test"}',
        approver_user_ids=["approver-1", "approver-2", "approver-1"],
        notification_recipient_user_ids=[],
        env="dev",
    )

    assert record.biz_type == "BOT_COLLABORATOR"
    assert record.biz_data == '{"source": "test"}'
    with db.orm_session() as session:
        approvers = (
            session.query(WorkOrderApproverModel)
            .filter(WorkOrderApproverModel.work_order_id == record.id)
            .order_by(WorkOrderApproverModel.approver_user_id)
            .all()
        )
        notifications = (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == record.id)
            .all()
        )
        approver_ids = [item.approver_user_id for item in approvers]
        notification_recipients = {item.recipient_user_id for item in notifications}
        notification_categories = [item.notification_category for item in notifications]
        approver_notification_id = notifications[0].id
    assert approver_ids == ["approver-1", "approver-2"]
    assert notification_recipients == {"approver-1", "approver-2"}
    assert all(
        category == NotificationCategory.APPROVAL.value
        for category in notification_categories
    )
    notification_detail = repository.get_notification(
        notification_id=approver_notification_id,
        recipient_user_id="approver-1",
        env="dev",
        mark_read=False,
    )
    assert notification_detail is not None
    assert notification_detail.can_approve is True

    with pytest.raises(WorkOrderAccessDeniedError):
        repository.process_approval(
            work_order_id=record.id,
            reviewer_user_id="not-an-approver",
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            env="dev",
        )

    result = repository.process_approval(
        work_order_id=record.id,
        reviewer_user_id="approver-1",
        decision=WorkOrderDecision.APPROVED,
        review_remark="approved",
        env="dev",
    )
    assert result.status is WorkOrderStatus.APPROVED
    assert result.decision is WorkOrderDecision.APPROVED

    with db.orm_session() as session:
        states = {
            item.approver_user_id: item.status
            for item in session.query(WorkOrderApproverModel)
            .filter(WorkOrderApproverModel.work_order_id == record.id)
            .all()
        }
        result_notifications = (
            session.query(WorkOrderNotificationModel)
            .filter(
                WorkOrderNotificationModel.work_order_id == record.id,
                WorkOrderNotificationModel.recipient_user_id == "applicant-42",
            )
            .all()
        )
    assert states == {"approver-1": "APPROVED", "approver-2": "CANCELLED"}
    assert len(result_notifications) == 1

    with pytest.raises(WorkOrderAlreadyProcessedError):
        repository.process_approval(
            work_order_id=record.id,
            reviewer_user_id="approver-2",
            decision=WorkOrderDecision.REJECTED,
            review_remark="too late",
            env="dev",
        )


def test_unified_space_join_approval_adds_member_in_same_transaction(db) -> None:
    spaces = SpaceRepository(db)
    repository = _work_orders(db)
    team = _team(spaces, name="Unified Join Team", creator="owner-unified")

    record = repository.create_space_join_request(
        space_id=team.id,
        applicant_user_id="applicant-unified",
        applicant_name="Applicant",
        apply_reason=None,
        env="dev",
    )

    result = repository.process_approval(
        work_order_id=record.id,
        reviewer_user_id="owner-unified",
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        env="dev",
    )

    assert result.status is WorkOrderStatus.APPROVED
    with db.orm_session() as session:
        order_status = (
            session.query(WorkOrderModel.status)
            .filter(WorkOrderModel.id == record.id)
            .scalar()
        )
        member_role, member_status, member_created_by, member_user_name = (
            session.query(
                SpaceMemberModel.role,
                SpaceMemberModel.status,
                SpaceMemberModel.created_by,
                SpaceMemberModel.user_name,
            )
            .filter(
                SpaceMemberModel.space_id == team.id,
                SpaceMemberModel.user_id == "applicant-unified",
            )
            .one()
        )
        notice_category = (
            session.query(WorkOrderNotificationModel.notification_category)
            .filter(
                WorkOrderNotificationModel.work_order_id == record.id,
                WorkOrderNotificationModel.recipient_user_id == "applicant-unified",
            )
            .scalar()
        )
    assert order_status == WorkOrderStatus.APPROVED.value
    assert member_role == SpaceRole.MEMBER.value
    assert member_status == "ACTIVE"
    assert member_created_by == "owner-unified"
    assert member_user_name == "applicant-unified"
    assert notice_category == NotificationCategory.NOTICE.value

    _, items = spaces.list_spaces(
        user_id="applicant-unified",
        env="dev",
        keyword=None,
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )
    joined = next(item for item in items if item.space.id == team.id)
    assert joined.join_status is SpaceJoinStatus.JOINED
    assert joined.current_user_role is SpaceRole.MEMBER


def test_unified_space_join_rejection_does_not_add_member(db) -> None:
    spaces = SpaceRepository(db)
    repository = _work_orders(db)
    team = _team(spaces, name="Unified Reject Team", creator="owner-reject")
    record = repository.create_space_join_request(
        space_id=team.id,
        applicant_user_id="applicant-reject",
        applicant_name="Applicant",
        apply_reason="join",
        env="dev",
    )

    result = repository.process_approval(
        work_order_id=record.id,
        reviewer_user_id="owner-reject",
        decision=WorkOrderDecision.REJECTED,
        review_remark="not now",
        env="dev",
    )

    assert result.status is WorkOrderStatus.REJECTED
    assert (
        spaces.get_member(space_id=team.id, user_id="applicant-reject", env="dev")
        is None
    )
    with db.orm_session() as session:
        notice_category = (
            session.query(WorkOrderNotificationModel.notification_category)
            .filter(
                WorkOrderNotificationModel.work_order_id == record.id,
                WorkOrderNotificationModel.recipient_user_id == "applicant-reject",
            )
            .scalar()
        )
    assert notice_category == NotificationCategory.NOTICE.value


def test_unified_space_join_approval_rolls_back_when_member_already_exists(db) -> None:
    spaces = SpaceRepository(db)
    repository = _work_orders(db)
    team = _team(spaces, name="Unified Rollback Team", creator="owner-rollback")
    record = repository.create_space_join_request(
        space_id=team.id,
        applicant_user_id="applicant-rollback",
        applicant_name="Applicant",
        apply_reason="join",
        env="dev",
    )
    spaces.add_member(
        space_id=team.id,
        user_id="applicant-rollback",
        role=SpaceRole.MEMBER,
        creator_id="seed",
        env="dev",
    )

    with pytest.raises(WorkOrderApplicantAlreadyMemberError):
        repository.process_approval(
            work_order_id=record.id,
            reviewer_user_id="owner-rollback",
            decision=WorkOrderDecision.APPROVED,
            review_remark="approve",
            env="dev",
        )

    with db.orm_session() as session:
        order_status = (
            session.query(WorkOrderModel.status)
            .filter(WorkOrderModel.id == record.id)
            .scalar()
        )
        approver_status = (
            session.query(WorkOrderApproverModel.status)
            .filter(WorkOrderApproverModel.work_order_id == record.id)
            .scalar()
        )
        notice_count = (
            session.query(WorkOrderNotificationModel.id)
            .filter(WorkOrderNotificationModel.work_order_id == record.id)
            .count()
        )
    assert order_status == WorkOrderStatus.PENDING.value
    assert approver_status == "PENDING"
    assert notice_count == 1


def test_unified_space_join_approval_rejects_missing_space_and_rolls_back(db) -> None:
    repository = _work_orders(db)
    with db.transactional_orm_session() as session:
        order = WorkOrderModel(
            work_order_no=repository._new_no(),
            biz_type=WorkOrderBizType.SPACE_JOIN.value,
            biz_id="999999",
            applicant_user_id="applicant-missing-space",
            apply_reason=None,
            status=WorkOrderStatus.PENDING.value,
            env="dev",
        )
        session.add(order)
        session.flush()
        session.add(
            WorkOrderApproverModel(
                work_order_id=order.id,
                approver_user_id="reviewer-missing-space",
                status=WorkOrderApproverStatus.PENDING.value,
                env="dev",
            )
        )
        work_order_id = order.id

    with pytest.raises(WorkOrderNotFoundError, match="business object not found"):
        repository.process_approval(
            work_order_id=work_order_id,
            reviewer_user_id="reviewer-missing-space",
            decision=WorkOrderDecision.APPROVED,
            review_remark=None,
            env="dev",
        )

    with db.orm_session() as session:
        order_status = (
            session.query(WorkOrderModel.status)
            .filter(WorkOrderModel.id == work_order_id)
            .scalar()
        )
        approver_status = (
            session.query(WorkOrderApproverModel.status)
            .filter(WorkOrderApproverModel.work_order_id == work_order_id)
            .scalar()
        )
        notice_count = (
            session.query(WorkOrderNotificationModel.id)
            .filter(WorkOrderNotificationModel.work_order_id == work_order_id)
            .count()
        )
    assert order_status == WorkOrderStatus.PENDING.value
    assert approver_status == WorkOrderApproverStatus.PENDING.value
    assert notice_count == 0


def test_unified_notice_work_order_does_not_create_approvers(db) -> None:
    repository = _work_orders(db)

    record = repository.create_work_order(
        biz_type="SPACE_MEMBER_ADDED",
        biz_id="space-42",
        applicant_user_id="system",
        apply_reason="member added",
        biz_data=None,
        approver_user_ids=[],
        notification_recipient_user_ids=["member-42"],
        env="dev",
    )

    with db.orm_session() as session:
        assert (
            session.query(WorkOrderApproverModel)
            .filter(WorkOrderApproverModel.work_order_id == record.id)
            .count()
            == 0
        )
        notification = (
            session.query(WorkOrderNotificationModel)
            .filter(WorkOrderNotificationModel.work_order_id == record.id)
            .one()
        )
        recipient_user_id = notification.recipient_user_id
        notification_category = notification.notification_category
    assert recipient_user_id == "member-42"
    assert notification_category == NotificationCategory.NOTICE.value


def test_market_favorite_repository_is_idempotent_and_searchable(db) -> None:
    spaces = SpaceRepository(db)
    space = _team(spaces)
    repository = MarketFavoriteRepository(db)

    first, first_changed = repository.add(
        space_id=space.id,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        created_by="owner-1",
        env="dev",
    )
    duplicate, duplicate_changed = repository.add(
        space_id=space.id,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        created_by="owner-1",
        env="dev",
    )
    repository.add(
        space_id=space.id,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.MCP,
        target_code="mcp-1",
        created_by="owner-1",
        env="dev",
    )
    tc_record, tc_changed = repository.add(
        space_id=space.id,
        market_source=MarketSource.TEAMCLAW,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        created_by="member-1",
        env="dev",
    )

    assert first_changed is True
    assert duplicate_changed is False
    assert duplicate.id == first.id
    assert tc_changed is True
    assert tc_record.id != first.id
    total, rows = repository.search(
        space_id=space.id,
        market_source=MarketSource.SKILLCENTER,
        target_type=FavoriteTargetType.SKILL,
        keyword="skill",
        env="dev",
        offset=0,
        limit=10,
    )
    assert total == 1
    assert rows[0].target_code == "skill-1"
    assert rows[0].market_source is MarketSource.SKILLCENTER
    assert repository.find_favorited_codes(
        space_id=space.id,
        market_source=MarketSource.TEAMCLAW,
        target_type=FavoriteTargetType.SKILL,
        target_codes=["skill-1", "missing"],
        env="dev",
    ) == {"skill-1"}
    assert (
        repository.cancel(
            space_id=space.id,
            market_source=MarketSource.SKILLCENTER,
            target_type=FavoriteTargetType.SKILL,
            target_code="skill-1",
            env="dev",
        )
        is True
    )
    assert (
        repository.cancel(
            space_id=space.id,
            market_source=MarketSource.SKILLCENTER,
            target_type=FavoriteTargetType.SKILL,
            target_code="skill-1",
            env="dev",
        )
        is False
    )


def test_work_order_repository_approve_and_notification_lifecycle(db) -> None:
    spaces = SpaceRepository(db)
    space = _team(spaces)
    repository = _work_orders(db)

    record = repository.create_space_join_request(
        space_id=space.id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="join",
        env="dev",
    )
    assert record.status is WorkOrderStatus.PENDING
    assert record.work_order_no.startswith("WO")
    assert record.biz_data is None
    with pytest.raises(WorkOrderAlreadyPendingError):
        repository.create_space_join_request(
            space_id=space.id,
            applicant_user_id="applicant-1",
            applicant_name="Applicant",
            apply_reason="join",
            env="dev",
        )
    with pytest.raises(WorkOrderNotFoundError):
        repository.create_space_join_request(
            space_id=999,
            applicant_user_id="applicant-1",
            applicant_name="Applicant",
            apply_reason="join",
            env="dev",
        )

    total, pending = repository.list_items(
        actor_id="owner-1",
        env="dev",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.ALL,
        offset=0,
        limit=20,
    )
    assert total == 1
    assert pending[0].can_approve is True
    initiated_total, initiated = repository.list_items(
        actor_id="applicant-1",
        env="dev",
        query_type=WorkOrderQueryType.INITIATED_BY_ME,
        item_type=WorkOrderItemType.APPROVAL,
        offset=0,
        limit=20,
    )
    assert initiated_total == 1
    assert initiated[0].notification is None
    repository.list_items(
        actor_id="owner-1",
        env="dev",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.APPROVAL,
        offset=0,
        limit=20,
    )
    repository.list_items(
        actor_id="applicant-1",
        env="dev",
        query_type=WorkOrderQueryType.INITIATED_BY_ME,
        item_type=WorkOrderItemType.NOTICE,
        offset=0,
        limit=20,
    )

    owner_detail = repository.get_detail(
        work_order_id=record.id, actor_id="owner-1", env="dev"
    )
    assert owner_detail.event_type is not None
    assert owner_detail.title is not None
    assert owner_detail.can_approve is True
    assert (
        repository.get_detail(work_order_id=record.id, actor_id="intruder", env="dev")
        is None
    )
    assert (
        repository.get_detail(work_order_id=999, actor_id="owner-1", env="dev") is None
    )

    notification = pending[0].notification
    assert notification.title == "空间加入申请待审批"
    assert repository.count_unread(recipient_user_id="owner-1", env="dev") == 1
    owner_badge = repository.get_notification_badge_summary(
        recipient_user_id="owner-1", env="dev"
    )
    assert owner_badge.model_dump() == {
        "unread_count": 1,
        "pending_approval_count": 1,
        "unread_notice_count": 0,
        "badge_count": 1,
    }
    detail = repository.get_notification(
        notification_id=notification.id,
        recipient_user_id="owner-1",
        env="dev",
        mark_read=True,
    )
    assert detail.notification.is_read is True
    assert detail.can_approve is True
    assert repository.get_notification_badge_summary(
        recipient_user_id="owner-1", env="dev"
    ).model_dump() == {
        "unread_count": 0,
        "pending_approval_count": 1,
        "unread_notice_count": 0,
        "badge_count": 1,
    }
    assert (
        repository.mark_notification_read(
            notification_id=999, recipient_user_id="owner-1", env="dev"
        )
        is None
    )

    with pytest.raises(WorkOrderAccessDeniedError):
        repository.review_space_join(
            work_order_id=record.id,
            reviewer_user_id="intruder",
            review_remark="ok",
            target_status=WorkOrderStatus.APPROVED,
            notification=_review_notification(
                applicant_user_id="applicant-1",
                space_id=space.id,
                title="approved",
                content="approved content",
            ),
            applicant_user_name="Applicant",
            env="dev",
        )
    approved_notification = _review_notification(
        applicant_user_id="applicant-1",
        space_id=space.id,
        title="custom approved title",
        content="custom approved content",
    )
    result = repository.review_space_join(
        work_order_id=record.id,
        reviewer_user_id="owner-1",
        review_remark="ok",
        target_status=WorkOrderStatus.APPROVED,
        notification=approved_notification,
        applicant_user_name="Applicant",
        env="dev",
    )
    assert result.status is WorkOrderStatus.APPROVED
    member = spaces.get_member(space_id=space.id, user_id="applicant-1", env="dev")
    assert member is not None
    assert member.user_name == "Applicant"
    _, joined_spaces = spaces.list_spaces(
        user_id="applicant-1",
        env="dev",
        keyword=None,
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )
    assert (
        next(item for item in joined_spaces if item.space.id == space.id).join_status
        is SpaceJoinStatus.JOINED
    )
    assert (
        repository.get_notification_badge_summary(
            recipient_user_id="owner-1", env="dev"
        ).pending_approval_count
        == 0
    )
    with pytest.raises(WorkOrderAlreadyProcessedError):
        repository.review_space_join(
            work_order_id=record.id,
            reviewer_user_id="owner-1",
            review_remark="again",
            target_status=WorkOrderStatus.REJECTED,
            notification=_review_notification(
                applicant_user_id="applicant-1",
                space_id=space.id,
                title="rejected",
                content="rejected content",
            ),
            applicant_user_name=None,
            env="dev",
        )

    processed_total, processed = repository.list_items(
        actor_id="owner-1",
        env="dev",
        query_type=WorkOrderQueryType.PROCESSED_BY_ME,
        item_type=WorkOrderItemType.ALL,
        offset=0,
        limit=20,
    )
    assert processed_total == 1
    assert processed[0].work_order.status is WorkOrderStatus.APPROVED
    applicant_total, applicant_items = repository.list_items(
        actor_id="applicant-1",
        env="dev",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.NOTICE,
        offset=0,
        limit=20,
    )
    assert applicant_total == 1
    applicant_notification = applicant_items[0].notification
    assert applicant_notification.title == "空间加入申请已处理"
    assert applicant_notification.content == approved_notification.content
    assert (
        repository.list_items(
            actor_id="applicant-1",
            env="dev",
            query_type=WorkOrderQueryType.PROCESSED_BY_ME,
            item_type=WorkOrderItemType.NOTICE,
            offset=0,
            limit=20,
        )[0]
        == 0
    )
    applicant_badge = repository.get_notification_badge_summary(
        recipient_user_id="applicant-1", env="dev"
    )
    assert applicant_badge.model_dump() == {
        "unread_count": 1,
        "pending_approval_count": 0,
        "unread_notice_count": 1,
        "badge_count": 1,
    }
    assert (
        repository.mark_notification_read(
            notification_id=applicant_notification.id,
            recipient_user_id="applicant-1",
            env="dev",
        ).is_read
        is True
    )
    processed_notice_total, processed_notices = repository.list_items(
        actor_id="applicant-1",
        env="dev",
        query_type=WorkOrderQueryType.PROCESSED_BY_ME,
        item_type=WorkOrderItemType.NOTICE,
        offset=0,
        limit=20,
    )
    assert processed_notice_total == 1
    assert processed_notices[0].notification.id == applicant_notification.id
    assert (
        repository.get_notification_badge_summary(
            recipient_user_id="applicant-1", env="dev"
        ).badge_count
        == 0
    )
    assert (
        repository.mark_all_notifications_read(
            recipient_user_id="applicant-1", env="dev"
        )
        == 0
    )


def test_work_order_repository_rejects_and_requires_reviewer(db) -> None:
    spaces = SpaceRepository(db)
    no_owner = SpaceModel(
        space_code="spc-no-owner",
        space_type=SpaceType.TEAM.value,
        name="No Owner",
        personal_owner_id=None,
        env="dev",
        created_by="creator",
        updated_by="creator",
    )
    with db.orm_session() as session:
        session.add(no_owner)
        session.flush()
        session.refresh(no_owner)
        no_owner_id = no_owner.id
    repository = _work_orders(db)
    with pytest.raises(WorkOrderNoReviewerError):
        repository.create_space_join_request(
            space_id=no_owner_id,
            applicant_user_id="applicant-1",
            applicant_name="Applicant",
            apply_reason="join",
            env="dev",
        )

    team = _team(spaces, name="Reject Team", creator="owner-2")
    record = repository.create_space_join_request(
        space_id=team.id,
        applicant_user_id="applicant-2",
        applicant_name="Applicant 2",
        apply_reason="join",
        env="dev",
    )
    result = repository.review_space_join(
        work_order_id=record.id,
        reviewer_user_id="owner-2",
        review_remark="capacity",
        target_status=WorkOrderStatus.REJECTED,
        notification=_review_notification(
            applicant_user_id="applicant-2",
            space_id=team.id,
            title="custom rejected title",
            content="custom rejected content",
        ),
        applicant_user_name=None,
        env="dev",
    )
    assert result.status is WorkOrderStatus.REJECTED
    assert spaces.get_member(space_id=team.id, user_id="applicant-2", env="dev") is None
    _, rejected_spaces = spaces.list_spaces(
        user_id="applicant-2",
        env="dev",
        keyword=None,
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )
    assert (
        next(item for item in rejected_spaces if item.space.id == team.id).join_status
        is SpaceJoinStatus.NOT_JOINED
    )
    _, applicant_items = repository.list_items(
        actor_id="applicant-2",
        env="dev",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.NOTICE,
        offset=0,
        limit=20,
    )
    assert (
        applicant_items[0].notification.title
        == "空间加入申请已处理"
    )
    assert applicant_items[0].notification.content == "custom rejected content"


def test_badge_counts_distinct_pending_work_orders(db) -> None:
    spaces = SpaceRepository(db)
    space = _team(spaces, name="Badge Team")
    repository = _work_orders(db)
    record = repository.create_space_join_request(
        space_id=space.id,
        applicant_user_id="applicant-badge",
        applicant_name="Applicant",
        apply_reason="join",
        env="dev",
    )
    with db.orm_session() as session:
        session.add(
            WorkOrderNotificationModel(
                work_order_id=record.id,
                recipient_user_id="owner-1",
                notification_category=NotificationCategory.APPROVAL.value,
                event_type=WorkOrderEventType.SPACE_JOIN_APPLIED.value,
                biz_type=WorkOrderBizType.SPACE_JOIN.value,
                biz_id=str(space.id),
                title="duplicate approval",
                content="duplicate",
                env="dev",
            )
        )
        session.flush()

    summary = repository.get_notification_badge_summary(
        recipient_user_id="owner-1", env="dev"
    )
    assert summary.unread_count == 2
    assert summary.pending_approval_count == 1
    assert summary.badge_count == 1


def test_friend_approval_context_and_reviewed_event_use_original_applied_event(
    db,
) -> None:
    repository = WorkOrderRepository(db, _skill_editor_requests(db))
    created = repository.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        biz_id="legacy-id",
        event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
        applicant_user_id="applicant-friend",
        approver_user_ids=["reviewer-friend"],
        recipient_user_ids=[],
        title="friend approval",
        content=None,
        apply_reason=None,
        biz_data=json.dumps({"request_ids": ["request-88"]}),
        env="dev",
    )
    assert created.work_order_id is not None

    context = repository.get_approval_context(
        work_order_id=created.work_order_id,
        reviewer_user_id="reviewer-friend",
        env="dev",
    )

    assert context.source_event_type == WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value
    assert context.work_order.status is WorkOrderStatus.PENDING
    assert context.approver.status is WorkOrderApproverStatus.PENDING

    repository.process_approval(
        work_order_id=created.work_order_id,
        reviewer_user_id="reviewer-friend",
        decision=WorkOrderDecision.APPROVED,
        review_remark=None,
        env="dev",
    )

    with db.orm_session() as session:
        result_event_type = (
            session.query(WorkOrderNotificationModel.event_type)
            .filter(
                WorkOrderNotificationModel.work_order_id == created.work_order_id,
                WorkOrderNotificationModel.recipient_user_id == "applicant-friend",
                WorkOrderNotificationModel.notification_category
                == NotificationCategory.NOTICE.value,
            )
            .scalar()
        )
    assert result_event_type == WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED.value


def test_get_approval_context_rejects_missing_order(db) -> None:
    repository = WorkOrderRepository(db, _skill_editor_requests(db))

    with pytest.raises(WorkOrderNotFoundError):
        repository.get_approval_context(
            work_order_id=999,
            reviewer_user_id="reviewer-friend",
            env="dev",
        )


def test_get_approval_context_rejects_non_approver(db) -> None:
    repository = WorkOrderRepository(db, _skill_editor_requests(db))
    created = repository.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        biz_id="friend-id",
        event_type=WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value,
        applicant_user_id="applicant-friend",
        approver_user_ids=["reviewer-friend"],
        recipient_user_ids=[],
        title="friend approval",
        content=None,
        apply_reason=None,
        biz_data=json.dumps({"request_ids": ["request-99"]}),
        env="dev",
    )
    assert created.work_order_id is not None

    with pytest.raises(WorkOrderAccessDeniedError):
        repository.get_approval_context(
            work_order_id=created.work_order_id,
            reviewer_user_id="other-user",
            env="dev",
        )
