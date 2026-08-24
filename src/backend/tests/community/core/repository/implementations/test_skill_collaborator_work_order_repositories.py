"""Tests for generic Skill collaborator work-order persistence."""

import asyncio

import pytest

from agentclaw.community.core.repository.implementations.work_orders.work_order import (
    WorkOrderRepository,
)
from agentclaw.community.core.work_orders.models import (
    WorkOrderBizType,
    WorkOrderItemType,
    WorkOrderQueryType,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests


@pytest.fixture
def db():
    reset_for_tests()
    plugin = SqliteDB()
    asyncio.run(plugin.bootstrap())
    yield plugin
    reset_for_tests()


def test_unified_work_order_accepts_skill_collaborator_biz_type(db) -> None:
    repository = WorkOrderRepository(db)

    record = repository.create_work_order(
        biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
        biz_id="skill-42",
        applicant_user_id="applicant-42",
        apply_reason=None,
        biz_data='{"skill_id": "skill-42"}',
        approver_user_ids=[],
        notification_recipient_user_ids=["recipient-1"],
        env="dev",
    )

    assert record.biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value

    total, items = repository.list_items(
        actor_id="recipient-1",
        env="dev",
        query_type=WorkOrderQueryType.PENDING_FOR_ME,
        item_type=WorkOrderItemType.ALL,
        biz_type=WorkOrderBizType.SKILL_COLLABORATOR.value,
        biz_id="skill-42",
        offset=0,
        limit=20,
    )
    assert total == 1
    assert items[0].work_order.biz_type == WorkOrderBizType.SKILL_COLLABORATOR.value
