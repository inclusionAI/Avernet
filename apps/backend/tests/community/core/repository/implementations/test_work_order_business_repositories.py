"""Work-order event and business regression cases split by responsibility."""

import json
import pytest
from agentclaw.community.core.bot_collaborator.models import BotCollaboratorModel
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.models.space_skill import SkillGrant
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
)
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderNotFoundError,
    WorkOrderSkillEditorRequestNotAllowedError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderApprovalMode,
    WorkOrderBizType,
    WorkOrderDecision,
    WorkOrderApproverStatus,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderQueryType,
    WorkOrderStatus,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderApproverModel,
    WorkOrderNotificationModel,
    WorkOrderModel,
)
from agentclaw.community.plugin_api.models import BotModel

from tests.community.core.repository.implementations.test_space_market_work_order_repositories import (
    _bot_review_notification,
    _skill_editor_requests,
    _skill_review_notification,
    _space_skill,
    _space_skills,
    _team,
    _work_orders,
    db as db,
)


def test_auto_event_is_created_processing_without_result_notice(db) -> None:
    repository = _work_orders(db)
    created = repository.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        biz_id="friend-auto-1",
        event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
        applicant_user_id="applicant-auto",
        approver_user_ids=['applicant-auto'],
        recipient_user_ids=[],
        title="friend request",
        content=None,
        apply_reason=None,
        biz_data=json.dumps({"request_ids": ["request-auto-1"]}),
        env="dev",
    )
    assert created.status.value == "PROCESSING"
    with db.orm_session() as session:
        order = session.query(WorkOrderModel).one()
        assert order.status == WorkOrderStatus.PROCESSING.value
        assert session.query(WorkOrderApproverModel).count() == 0
        assert session.query(WorkOrderNotificationModel).count() == 0


def test_auto_finalize_records_system_reviewer_without_approver_rows(db) -> None:
    repository = _work_orders(db)
    created = repository.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        approval_mode=WorkOrderApprovalMode.AUTO,
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        biz_id="friend-auto-finalize",
        event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
        applicant_user_id="applicant-auto",
        approver_user_ids=['applicant-auto'],
        recipient_user_ids=[],
        title="friend request",
        content=None,
        apply_reason=None,
        biz_data=json.dumps({"request_ids": ["request-auto-finalize"]}),
        env="dev",
    )
    repository.complete_auto_approval(
        work_order_id=created.work_order_id,
        recipient_user_ids=["applicant-auto"],
        source_event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
        env="dev",
    )

    with db.orm_session() as session:
        order = (
            session.query(WorkOrderModel)
            .filter(WorkOrderModel.id == created.work_order_id)
            .one()
        )
        status = order.status
        reviewer_user_id = order.reviewer_user_id
        approver_count = (
            session.query(WorkOrderApproverModel)
            .filter(WorkOrderApproverModel.work_order_id == created.work_order_id)
            .count()
        )
    assert status == WorkOrderStatus.APPROVED.value
    assert reviewer_user_id == "SYSTEM"
    assert approver_count == 0


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


def test_skill_editor_request_persists_display_name_and_work_no(db) -> None:
    spaces = SpaceRepository(db)
    team, skill_id = _space_skill(db, spaces)
    spaces.add_member(
        space_id=team.id,
        user_id="200177",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    repository = _work_orders(db)

    order = repository.create_skill_editor_request(
        space_id=team.id,
        skill_id=skill_id,
        applicant_user_id="200177",
        applicant_name="张三",
        apply_reason="maintain together",
        env="dev",
    )

    with db.orm_session() as session:
        content = (
            session.query(WorkOrderNotificationModel)
            .filter(
                WorkOrderNotificationModel.work_order_id == order.id,
                WorkOrderNotificationModel.env == "dev",
            )
            .one()
            .content
        )

    assert content == (
        "用户「张三」(200177)申请共同编辑 Skill「review-skill」，请及时处理。"
    )


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
        result = (
            session.query(
                WorkOrderNotificationModel.event_type,
                WorkOrderNotificationModel.title,
                WorkOrderNotificationModel.content,
            )
            .filter(
                WorkOrderNotificationModel.work_order_id == created.work_order_id,
                WorkOrderNotificationModel.recipient_user_id == "applicant-friend",
                WorkOrderNotificationModel.notification_category
                == NotificationCategory.NOTICE.value,
            )
            .one()
        )
    assert result.event_type == WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED.value
    assert result.title == "Bot 好友申请已通过"
    assert json.loads(result.content) == {"text": "你的 Bot 好友申请已通过。"}


def test_friend_rejection_persists_status_specific_content(db) -> None:
    repository = WorkOrderRepository(db, _skill_editor_requests(db))
    created = repository.create_work_order_event(
        event_category=NotificationCategory.APPROVAL,
        biz_type=WorkOrderBizType.BOT_FRIEND.value,
        biz_id="legacy-id-reject",
        event_type=WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value,
        applicant_user_id="applicant-reject",
        approver_user_ids=["reviewer-reject"],
        recipient_user_ids=[],
        title="friend approval",
        content=None,
        apply_reason=None,
        biz_data=json.dumps({"request_ids": ["request-reject"]}),
        env="dev",
    )
    assert created.work_order_id is not None

    repository.process_approval(
        work_order_id=created.work_order_id,
        reviewer_user_id="reviewer-reject",
        decision=WorkOrderDecision.REJECTED,
        review_remark="审批备注",
        env="dev",
    )

    with db.orm_session() as session:
        result = (
            session.query(
                WorkOrderNotificationModel.event_type,
                WorkOrderNotificationModel.title,
                WorkOrderNotificationModel.content,
            )
            .filter(
                WorkOrderNotificationModel.work_order_id == created.work_order_id,
                WorkOrderNotificationModel.recipient_user_id == "applicant-reject",
                WorkOrderNotificationModel.notification_category
                == NotificationCategory.NOTICE.value,
            )
            .one()
        )
    assert result.event_type == WorkOrderEventType.BOT2BOT_FRIEND_REVIEWED.value
    assert result.title == "Bot 好友申请未通过"
    assert json.loads(result.content) == {
        "text": "你的 Bot 好友申请未通过。",
        "review_remark": "审批备注",
    }


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
