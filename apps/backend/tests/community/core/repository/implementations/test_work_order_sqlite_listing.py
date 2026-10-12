"""File SQLite journeys through services, list queries and HTTP presentation."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from agentclaw.community.adapters.http.openapi_v1.work_orders.router import _list_item
from agentclaw.community.core.work_orders.errors import (
    WorkOrderLocalFinalizeError,
    WorkOrderNotFoundError,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderDecision,
    WorkOrderItemType,
    WorkOrderQueryType,
)
from agentclaw.community.core.work_orders.repository.models import (
    WorkOrderModel,
    WorkOrderNotificationModel,
)
from tests.community.core.repository.implementations.test_work_order_sqlite_journeys import (
    friend,
    reject_result_notice,
    review,
    world as _sqlite_world,
)


world = _sqlite_world


def listing(w, actor, query, item_type=WorkOrderItemType.ALL, **kwargs):
    total, items = w.service.list_items(
        actor_id=actor,
        query_type=query,
        item_type=item_type,
        page_no=kwargs.pop("page_no", 1),
        page_size=kwargs.pop("page_size", 100),
        **kwargs,
    )
    result = [_list_item(item) for item in items]
    print(
        f"LIST {actor}/{query.value}/{item_type.value}: total={total}, "
        f"rows={[(r.work_order_id, r.item_type.value, r.title, r.is_read, r.can_approve) for r in result]}"
    )
    return total, result


def create(w, kind, auto):
    if kind == "friend":
        return friend(w, auto).work_order_id
    if kind == "skill":
        w.entry.update_approval_policy(
            space_id=w.space_id,
            skill_id=w.skill_id,
            actor_id="owner-1",
            auto_approve_editor_requests=auto,
        )
        return w.entry.create_request(
            space_id=w.space_id,
            skill_id=w.skill_id,
            applicant_user_id="applicant-1",
            reason="edit",
        ).work_order_id
    w.service.update_bot_editor_request_policy(
        bot_id="journey-bot",
        owner_id="owner-1",
        actor_id="owner-1",
        auto_approve=auto,
    )
    return w.service.create_bot_editor_request(
        bot_id="journey-bot",
        owner_id="owner-1",
        applicant_user_id="applicant-1",
        reason="edit",
    ).id


@pytest.mark.parametrize("kind", ["skill", "friend", "bot"])
@pytest.mark.parametrize("mode", ["auto", "approve", "reject"])
def test_end_to_end_inbox_and_initiated_projection(world, kind, mode):
    w = world
    auto = mode == "auto"
    order_id = create(w, kind, auto)
    if not auto:
        total, pending = listing(w, "owner-1", WorkOrderQueryType.PENDING_FOR_ME)
        assert total == 1 and pending[0].can_approve
        review(
            w,
            order_id,
            WorkOrderDecision.REJECTED
            if mode == "reject"
            else WorkOrderDecision.APPROVED,
        )
    for actor in ("owner-1", "applicant-1"):
        assert listing(w, actor, WorkOrderQueryType.PENDING_FOR_ME)[0] == 0
    recipient = "owner-1" if kind == "friend" and auto else "applicant-1"
    total, notices = listing(
        w, recipient, WorkOrderQueryType.PROCESSED_BY_ME, WorkOrderItemType.NOTICE
    )
    assert total == 1
    notice = notices[0]
    assert notice.work_order_id == order_id
    assert notice.is_read is False and notice.can_approve is False
    assert notice.status.value == ("REJECTED" if mode == "reject" else "APPROVED")
    if auto and kind != "bot":
        assert "自动审批" in notice.title
    for item_type in (WorkOrderItemType.ALL, WorkOrderItemType.APPROVAL):
        total, initiated = listing(
            w, "applicant-1", WorkOrderQueryType.INITIATED_BY_ME, item_type
        )
        assert total == 1
        assert initiated[0].work_order_id == order_id
        assert initiated[0].item_type.value == "APPROVAL"
        assert initiated[0].notification_id is None
        assert initiated[0].can_approve is False
        if auto and kind != "bot":
            assert "自动审批" in initiated[0].title
    detail = w.service.get_detail(work_order_id=order_id, actor_id="applicant-1")
    assert detail.work_order.id == order_id and detail.can_approve is False
    for query in WorkOrderQueryType:
        assert listing(w, "unrelated", query)[0] == 0
    with pytest.raises(WorkOrderNotFoundError):
        w.service.get_detail(work_order_id=order_id, actor_id="unrelated")
    before = w.repo.get_notification_badge_summary(
        recipient_user_id=recipient, env="dev"
    )
    assert before.pending_approval_count == 0 and before.unread_notice_count == 1
    w.repo.mark_notification_read(
        notification_id=notice.notification_id, recipient_user_id=recipient, env="dev"
    )
    after = w.repo.get_notification_badge_summary(
        recipient_user_id=recipient, env="dev"
    )
    assert after.unread_notice_count == after.badge_count == 0
    assert listing(w, recipient, WorkOrderQueryType.PENDING_FOR_ME)[0] == 0
    assert listing(
        w, recipient, WorkOrderQueryType.PROCESSED_BY_ME, WorkOrderItemType.NOTICE
    )[1][0].is_read
    with w.db.orm_session() as session:
        assert session.get(WorkOrderModel, order_id).status == notice.status.value
    assert w.path.is_file()


def test_auto_failure_is_processed_even_when_notice_unread(world):
    w = world
    w.http.post.side_effect = TimeoutError("unknown external outcome")
    order_id = friend(w, True).work_order_id
    assert listing(w, "owner-1", WorkOrderQueryType.PENDING_FOR_ME)[0] == 0
    _, rows = listing(w, "owner-1", WorkOrderQueryType.PROCESSED_BY_ME)
    assert len(rows) == 1 and rows[0].status.value == "FAILED"
    assert rows[0].is_read is False and rows[0].can_approve is False
    assert "失败" in rows[0].title and "自动审批" in rows[0].title
    _, initiated = listing(w, "applicant-1", WorkOrderQueryType.INITIATED_BY_ME)
    assert initiated[0].work_order_id == order_id
    assert "失败" in initiated[0].title and "自动审批" in initiated[0].title


def test_processing_order_is_not_misrepresented_as_finished(world):
    w = world
    event.listen(Session, "before_flush", reject_result_notice)
    try:
        with pytest.raises(WorkOrderLocalFinalizeError):
            friend(w, True)
    finally:
        event.remove(Session, "before_flush", reject_result_notice)
    for query in (
        WorkOrderQueryType.PENDING_FOR_ME,
        WorkOrderQueryType.PROCESSED_BY_ME,
    ):
        assert listing(w, "owner-1", query)[0] == 0
    _, initiated = listing(w, "applicant-1", WorkOrderQueryType.INITIATED_BY_ME)
    assert initiated[0].status.value == "PROCESSING"
    assert initiated[0].can_approve is False
    assert "处理中" in initiated[0].title and "自动审批" in initiated[0].title


def test_independent_notice_still_moves_on_read(world):
    w = world
    with w.db.orm_session() as session:
        notice = WorkOrderNotificationModel(
            recipient_user_id="applicant-1",
            notification_category="NOTICE",
            event_type="TASK_DISCOVERED",
            biz_type="TASK",
            biz_id="task-1",
            title="task",
            content="{}",
            env="dev",
        )
        session.add(notice)
        session.flush()
        notice_id = notice.id
    assert listing(w, "applicant-1", WorkOrderQueryType.PENDING_FOR_ME)[0] == 1
    assert listing(w, "applicant-1", WorkOrderQueryType.PROCESSED_BY_ME)[0] == 0
    w.repo.mark_notification_read(
        notification_id=notice_id, recipient_user_id="applicant-1", env="dev"
    )
    assert listing(w, "applicant-1", WorkOrderQueryType.PENDING_FOR_ME)[0] == 0
    assert listing(w, "applicant-1", WorkOrderQueryType.PROCESSED_BY_ME)[0] == 1


def test_initiated_orders_do_not_duplicate_or_depend_on_notice_read_time(world):
    w = world
    first = create(w, "skill", True)
    second = create(w, "friend", True)
    with w.db.orm_session() as session:
        original = (
            session.query(WorkOrderNotificationModel)
            .filter_by(work_order_id=first)
            .one()
        )
        session.add(
            WorkOrderNotificationModel(
                work_order_id=first,
                recipient_user_id="applicant-1",
                notification_category="NOTICE",
                event_type=original.event_type,
                biz_type=original.biz_type,
                biz_id=original.biz_id,
                title=original.title,
                content=original.content,
                env="dev",
            )
        )
    seen = []
    for page in (1, 2):
        total, rows = listing(
            w,
            "applicant-1",
            WorkOrderQueryType.INITIATED_BY_ME,
            WorkOrderItemType.APPROVAL,
            page_no=page,
            page_size=1,
        )
        assert total == 2 and len(rows) == 1
        seen.append(rows[0].work_order_id)
    assert set(seen) == {first, second}
    with w.db.orm_session() as session:
        # Reading a notice changes its timestamp, not the applicant's order sort.
        for notice in session.query(WorkOrderNotificationModel).filter_by(
            work_order_id=seen[-1]
        ):
            notice.is_read = True
            notice.gmt_modified = datetime.now() + timedelta(days=1)
    assert [
        row.work_order_id
        for row in listing(w, "applicant-1", WorkOrderQueryType.INITIATED_BY_ME)[1]
    ] == seen
    assert (
        listing(
            w,
            "applicant-1",
            WorkOrderQueryType.INITIATED_BY_ME,
            WorkOrderItemType.NOTICE,
        )[0]
        == 2
    )
    with w.db.orm_session() as session:
        biz_id = session.get(WorkOrderModel, first).biz_id
    total, rows = listing(
        w, "applicant-1", WorkOrderQueryType.INITIATED_BY_ME, biz_id=biz_id
    )
    assert total == 1 and rows[0].work_order_id == first
    assert (
        w.repo.list_items(
            actor_id="applicant-1",
            env="other",
            query_type=WorkOrderQueryType.INITIATED_BY_ME,
            item_type=WorkOrderItemType.ALL,
            offset=0,
            limit=10,
        )[0]
        == 0
    )


@pytest.mark.parametrize(
    "decision", [WorkOrderDecision.APPROVED, WorkOrderDecision.REJECTED]
)
def test_space_join_manual_lifecycle_remains_consistent(world, decision):
    w = world
    order = w.service.create_space_join_request(
        space_id=w.space_id, applicant_user_id="new-member", reason="join"
    )
    assert listing(w, "owner-1", WorkOrderQueryType.PENDING_FOR_ME)[1][0].can_approve
    review(w, order.id, decision)
    assert listing(w, "owner-1", WorkOrderQueryType.PENDING_FOR_ME)[0] == 0
    assert listing(w, "new-member", WorkOrderQueryType.PENDING_FOR_ME)[0] == 0
    _, rows = listing(w, "new-member", WorkOrderQueryType.PROCESSED_BY_ME)
    assert rows[0].status.value == decision.value and rows[0].is_read is False
    assert (
        listing(
            w,
            "new-member",
            WorkOrderQueryType.INITIATED_BY_ME,
            WorkOrderItemType.APPROVAL,
        )[0]
        == 1
    )


def test_excluded_bot_policy_auto_marker_gap_is_visible(world):
    """Characterize the excluded path; do not silently claim its AUTO label works."""
    w = world
    order_id = create(w, "bot", True)
    with w.db.orm_session() as session:
        order = session.get(WorkOrderModel, order_id)
        assert order.status == "APPROVED"
        assert order.to_record().approval_mode.value == "MANUAL"
    _, rows = listing(w, "applicant-1", WorkOrderQueryType.INITIATED_BY_ME)
    assert "自动审批" not in rows[0].title
