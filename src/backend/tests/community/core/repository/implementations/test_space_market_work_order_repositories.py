"""SQLite-backed unit tests for the new unified ORM repositories."""

import asyncio
from datetime import datetime

import pytest

from agentclaw.community.core.market_favorites.models import FavoriteTargetType
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
from agentclaw.community.core.spaces.models import SpaceRole, SpaceType
from agentclaw.community.core.spaces.repository.models import SpaceModel
from agentclaw.community.core.work_orders.errors import (
    WorkOrderAccessDeniedError,
    WorkOrderAlreadyPendingError,
    WorkOrderAlreadyProcessedError,
    WorkOrderNoReviewerError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    NotificationCategory,
    WorkOrderBizType,
    WorkOrderEventType,
    WorkOrderItemType,
    WorkOrderNotificationDraft,
    WorkOrderQueryType,
    WorkOrderStatus,
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
    assert personal_summary.current_user_role is SpaceRole.OWNER

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
    assert updated.role is SpaceRole.OWNER
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


def test_space_summary_is_env_isolated_and_excludes_soft_deleted_spaces(db) -> None:
    repository = SpaceRepository(db)
    team = _team(repository)
    repository.add_member(
        space_id=team.id,
        user_id="member-1",
        role=SpaceRole.MEMBER,
        creator_id="owner-1",
        env="dev",
    )

    summary = repository.get_space_summary(
        space_id=team.id, user_id="member-1", env="dev"
    )

    assert summary is not None
    assert summary.current_user_role is SpaceRole.MEMBER
    assert summary.member_count == 2
    assert repository.get_space_summary(
        space_id=team.id, user_id="member-1", env="pre"
    ) is None

    with db.orm_session() as session:
        row = session.query(SpaceModel).filter(SpaceModel.id == team.id).one()
        row.deleted_at = datetime(2026, 8, 19, 12, 0, 0)

    assert repository.get_space_summary(
        space_id=team.id, user_id="member-1", env="dev"
    ) is None


def test_market_favorite_repository_is_idempotent_and_searchable(db) -> None:
    spaces = SpaceRepository(db)
    space = _team(spaces)
    repository = MarketFavoriteRepository(db)

    first = repository.add(
        space_id=space.id,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        created_by="owner-1",
        env="dev",
    )
    duplicate = repository.add(
        space_id=space.id,
        target_type=FavoriteTargetType.SKILL,
        target_code="skill-1",
        created_by="owner-1",
        env="dev",
    )
    repository.add(
        space_id=space.id,
        target_type=FavoriteTargetType.MCP,
        target_code="mcp-1",
        created_by="owner-1",
        env="dev",
    )

    assert duplicate.id == first.id
    total, rows = repository.search(
        space_id=space.id,
        target_type=FavoriteTargetType.SKILL,
        keyword="skill",
        env="dev",
        offset=0,
        limit=10,
    )
    assert total == 1
    assert rows[0].target_code == "skill-1"
    assert (
        repository.cancel(
            space_id=space.id,
            target_type=FavoriteTargetType.SKILL,
            target_code="skill-1",
            env="dev",
        )
        is True
    )
    assert (
        repository.cancel(
            space_id=space.id,
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
    detail = repository.get_notification(
        notification_id=notification.id,
        recipient_user_id="owner-1",
        env="dev",
        mark_read=True,
    )
    assert detail.notification.is_read is True
    assert detail.can_approve is True
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
        repository.mark_notification_read(
            notification_id=applicant_notification.id,
            recipient_user_id="applicant-1",
            env="dev",
        ).is_read
        is True
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
