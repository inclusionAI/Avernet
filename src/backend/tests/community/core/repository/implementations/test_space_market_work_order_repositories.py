"""SQLite-backed unit tests for the new unified ORM repositories."""

import asyncio
import json
from datetime import datetime

import pytest

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
from agentclaw.community.core.spaces.errors import SpaceMemberAlreadyExistsError
from agentclaw.community.core.spaces.models import SpaceJoinStatus, SpaceRole, SpaceType
from agentclaw.community.core.spaces.repository.models import SpaceMemberModel, SpaceModel
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderApplicantAlreadyMemberError,
    WorkOrderNoReviewerError,
    WorkOrderNotFoundError,
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
        name=name, creator_id=creator, env="dev"
    ) as row:
        row.sc_team_id = f"sc-{name}-{creator}"
        return row


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


def test_unified_work_order_create_and_approval_lifecycle(db) -> None:
    repository = WorkOrderRepository(db)

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
    repository = WorkOrderRepository(db)
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
        member_role, member_status, member_created_by = (
            session.query(
                SpaceMemberModel.role,
                SpaceMemberModel.status,
                SpaceMemberModel.created_by,
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
    repository = WorkOrderRepository(db)
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
    assert spaces.get_member(
        space_id=team.id, user_id="applicant-reject", env="dev"
    ) is None
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
    repository = WorkOrderRepository(db)
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
    repository = WorkOrderRepository(db)
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
    repository = WorkOrderRepository(db)

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


def test_space_repository_full_member_lifecycle(db) -> None:
    repository = SpaceRepository(db)

    personal, created = repository.initialize_personal(user_id="user-1", env="dev")
    same, created_again = repository.initialize_personal(user_id="user-1", env="dev")
    team = _team(repository)

    assert created is True
    assert created_again is False
    assert same.id == personal.id
    assert (
        repository.get_space(space_id=team.id, env="dev").sc_team_id
        == "sc-Team-owner-1"
    )
    assert repository.get_space(space_id=999, env="dev") is None
    assert repository.get_space_by_code(space_code=team.space_code, env="dev") == team
    assert repository.get_space_by_code(space_code="missing", env="dev") is None

    other_env_personal, _ = repository.initialize_personal(
        user_id="user-other-env", env="pre"
    )
    batch = repository.batch_query_personal(
        user_ids=["missing", "user-1", "user-other-env"], env="dev"
    )
    assert [item.model_dump() for item in batch] == [
        {"user_id": "missing", "space_id": None, "found": False},
        {"user_id": "user-1", "space_id": personal.id, "found": True},
        {"user_id": "user-other-env", "space_id": None, "found": False},
    ]
    assert other_env_personal.id is not None

    total, spaces = repository.list_spaces(
        user_id="user-1", env="dev", keyword=None, space_type=None, offset=0, limit=20
    )
    assert total == 2
    assert {item.space.id for item in spaces} == {personal.id, team.id}
    personal_summary = next(item for item in spaces if item.space.id == personal.id)
    assert personal_summary.current_user_role is SpaceRole.ADMIN

    filtered_total, filtered = repository.list_spaces(
        user_id="owner-1",
        env="dev",
        keyword="Tea",
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )
    assert filtered_total == 1
    assert filtered[0].space.id == team.id

    added = repository.add_member(
        space_id=team.id,
        user_id="member-1",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    assert (
        repository.get_member(space_id=team.id, user_id="member-1", env="dev") == added
    )
    assert repository.get_member(space_id=team.id, user_id="missing", env="dev") is None
    with pytest.raises(SpaceMemberAlreadyExistsError):
        repository.add_member(
            space_id=team.id,
            user_id="member-1",
            role=SpaceRole.MEMBER,
            creator_id="owner-1",
            env="dev",
        )

    member_total, members = repository.list_members(
        space_id=team.id, env="dev", keyword="member", offset=0, limit=20
    )
    assert member_total == 1
    assert members[0].is_creator is False
    updated = repository.update_member_role(
        space_id=team.id, user_id="member-1", role=SpaceRole.OWNER, env="dev"
    )
    assert updated.role is SpaceRole.ADMIN
    assert (
        repository.update_member_role(
            space_id=team.id, user_id="missing", role=SpaceRole.OWNER, env="dev"
        )
        is None
    )
    assert (
        repository.delete_member(space_id=team.id, user_id="member-1", env="dev")
        is True
    )
    assert (
        repository.delete_member(space_id=team.id, user_id="member-1", env="dev")
        is False
    )


def test_space_repository_marks_pending_join_request_as_applying(db) -> None:
    spaces = SpaceRepository(db)
    work_orders = WorkOrderRepository(db)
    team = _team(spaces)
    other_team = _team(spaces, name="Other Team", creator="owner-2")

    work_orders.create_space_join_request(
        space_id=team.id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="join",
        env="dev",
    )

    total, items = spaces.list_spaces(
        user_id="applicant-1",
        env="dev",
        keyword=None,
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )

    assert total == 2
    statuses = {item.space.id: item.join_status for item in items}
    assert statuses[team.id] is SpaceJoinStatus.APPLYING
    assert statuses[other_team.id] is SpaceJoinStatus.NOT_JOINED

    spaces.add_member(
        space_id=team.id,
        user_id="applicant-1",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )
    _, joined_items = spaces.list_spaces(
        user_id="applicant-1",
        env="dev",
        keyword=None,
        space_type=SpaceType.TEAM.value,
        offset=0,
        limit=20,
    )
    joined = next(item for item in joined_items if item.space.id == team.id)
    assert joined.join_status is SpaceJoinStatus.JOINED


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
    repository = WorkOrderRepository(db)

    record = repository.create_space_join_request(
        space_id=space.id,
        applicant_user_id="applicant-1",
        applicant_name="Applicant",
        apply_reason="join",
        env="dev",
    )
    assert record.status is WorkOrderStatus.PENDING
    assert record.work_order_no.startswith("WO")
    assert json.loads(record.biz_data) == {
        "display_title": {"PENDING": "空间加入申请待审批"},
        "display_content": {
            "PENDING": "用户「Applicant」申请加入空间「Team」，请及时处理。"
        },
    }
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
    assert owner_detail.can_approve is True
    assert (
        repository.get_detail(work_order_id=record.id, actor_id="intruder", env="dev")
        is None
    )
    assert (
        repository.get_detail(work_order_id=999, actor_id="owner-1", env="dev") is None
    )

    notification = pending[0].notification
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
        env="dev",
    )
    assert result.status is WorkOrderStatus.APPROVED
    assert (
        spaces.get_member(space_id=space.id, user_id="applicant-1", env="dev")
        is not None
    )
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
    assert applicant_notification.title == approved_notification.title
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
    repository = WorkOrderRepository(db)
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
    assert applicant_items[0].notification.title == "custom rejected title"
    assert applicant_items[0].notification.content == "custom rejected content"


def test_badge_counts_distinct_pending_work_orders(db) -> None:
    spaces = SpaceRepository(db)
    space = _team(spaces, name="Badge Team")
    repository = WorkOrderRepository(db)
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


def test_space_repository_backfills_only_live_unbound_team_in_same_env(db) -> None:
    repository = SpaceRepository(db)
    target = SpaceModel(
        space_code="spc-repair-target",
        space_type=SpaceType.TEAM.value,
        name="Repair Target",
        personal_owner_id=None,
        sc_team_id=None,
        env="dev",
        created_by="owner",
        updated_by="owner",
    )
    personal = SpaceModel(
        space_code="spc-repair-personal",
        space_type=SpaceType.PERSONAL.value,
        name="Personal",
        personal_owner_id="personal-owner",
        sc_team_id=None,
        env="dev",
        created_by="personal-owner",
        updated_by="personal-owner",
    )
    already_bound = SpaceModel(
        space_code="spc-repair-bound",
        space_type=SpaceType.TEAM.value,
        name="Already Bound",
        personal_owner_id=None,
        sc_team_id="existing-team",
        env="dev",
        created_by="owner",
        updated_by="owner",
    )
    deleted = SpaceModel(
        space_code="spc-repair-deleted",
        space_type=SpaceType.TEAM.value,
        name="Deleted",
        personal_owner_id=None,
        sc_team_id=None,
        env="dev",
        created_by="owner",
        updated_by="owner",
        deleted_at=datetime(2026, 8, 20, 10, 0),
    )
    with db.orm_session() as session:
        session.add_all([target, personal, already_bound, deleted])
        session.flush()
        session.refresh(target)
        session.refresh(personal)
        session.refresh(already_bound)
        session.refresh(deleted)
        ids = {
            "target": target.id,
            "personal": personal.id,
            "bound": already_bound.id,
            "deleted": deleted.id,
        }

    assert (
        repository.backfill_sc_team_id(
            space_id=ids["target"], env="pre", sc_team_id="wrong-env"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["personal"], env="dev", sc_team_id="personal-team"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["bound"], env="dev", sc_team_id="replacement-team"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["deleted"], env="dev", sc_team_id="deleted-team"
        )
        is False
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["target"], env="dev", sc_team_id="repaired-team"
        )
        is True
    )
    assert (
        repository.backfill_sc_team_id(
            space_id=ids["target"], env="dev", sc_team_id="second-team"
        )
        is False
    )

    assert repository.get_space(space_id=ids["target"], env="dev").sc_team_id == (
        "repaired-team"
    )
    assert repository.get_space(space_id=ids["bound"], env="dev").sc_team_id == (
        "existing-team"
    )
    assert repository.get_space(space_id=ids["personal"], env="dev").sc_team_id is None
    assert repository.get_space(space_id=ids["deleted"], env="dev") is None
