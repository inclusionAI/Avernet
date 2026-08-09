"""Endpoint coverage for governance open + business-data router.

Covers the two 已开放接口(card-callback / offline-batch,免鉴权,有外部调用约定):
  - POST /card-callback            钉钉卡片回调(iframe fetch POST,治理反馈真入口)
  - POST /records/offline-batch    ODPS 离线批量写入(§7.2)

用户自助端点(/notifications*、用户 /whitelist)已从 router 删除:无真实用户主动
调用场景,治理反馈真入口是 card-callback。相关用例同步移除。

All tests use real DI services via ``world.get()`` and seed real data into
the in-memory SQLite database via repos. No MagicMock / unittest.mock.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from agentclaw.community.api.governance_service import (
    GovernanceFeedbackServiceProtocol,  # noqa: F401  (card-callback via feedback_svc.resolve)
    )
from agentclaw.community.core.economy.governance.orm import GovernanceNotificationOrm, GovernanceTicketOrm
from agentclaw.community.core.repository.implementations.governance.notify_log import NotifyLogRepository
from agentclaw.community.core.repository.implementations.governance.task_record import TaskRecordRepository
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_ID = "staff-001"


# ---------------------------------------------------------------------------
# Shared seed helpers — insert real data via repos
# ---------------------------------------------------------------------------


def _seed_open_ticket_for_user(
    world,
    *,
    owner_id: str = _USER_ID,
    bot_id: str = "bot-1",
    notification_id: str | None = None,
    governance_status: str = "open",
) -> str:
    """Insert a task_record + notify_log pair via repos so the feedback
    service's real queries can find it (card-callback 用)。

    Returns the ``notification_id`` used.
    """
    if notification_id is None:
        notification_id = uuid.uuid4().hex

    ticket_id = uuid.uuid4().hex
    worker_id = f"{owner_id}:{bot_id}"

    task_repo = world.get(TaskRecordRepository)
    task_repo.insert_ticket(
        GovernanceTicketOrm(
            ticket_id=ticket_id,
            worker_id=worker_id,
            active_worker=worker_id,
            bot_id=bot_id,
            owner_id=owner_id,
            bot_name="TestBot",
            dt_version="20260705",
            governance_decision="actionable",
            latest_decision="actionable",
            governance_status=governance_status,
            consecutive_normal_days=0,
            remind_count=0,
            last_sync_at=datetime.now(),
        ),
    )

    notify_repo = world.get(NotifyLogRepository)
    notify_repo.insert_notification(
        GovernanceNotificationOrm(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id=bot_id,
            bot_name="TestBot",
            owner_id=owner_id,
            worker_id=worker_id,
            dt_version="20260705",
            governance_decision="actionable",
            governance_cycle_id=ticket_id,
            governance_status=governance_status,
            notify_status="pending",
            notify_type="first_send",
            notify_source="offline_batch",
            send_attempt_count=0,
        ),
    )

    return notification_id


def _seed_happy_card_callback(world) -> None:
    """Seed an open ticket for card-callback (no auth)."""
    _seed_open_ticket_for_user(
        world, bot_id="bot-card", notification_id="n-uuid-card",
    )


def _seed_happy_offline_batch(world) -> None:
    """No pre-seeding needed — offline-batch creates tickets via the service."""


# ---------------------------------------------------------------------------
# Extra assertions (post-response state verification)
# ---------------------------------------------------------------------------


def _assert_card_callback_succeeded(response, world) -> None:
    """Verify card-callback resolve succeeded."""
    data = response.json()
    assert data["success"] is True


def _assert_offline_batch_succeeded(response, world) -> None:
    """Verify offline batch processed records."""
    data = response.json()
    assert data["success"] is True
    assert data["data"]["batch_id"] != ""


# ===========================================================================
# 1. POST /card-callback — DingTalk card iframe callback (no auth)
# ===========================================================================


@endpoint_test(
    method="POST",
    path="/api/economy/governance/card-callback",
    scenario="ok",
    input=CaseInput(
        json_body={
            "notification_id": "n-uuid-card",
            "response": "optimized",
            "remark": "已优化",
        },
    ),
    seed=_seed_happy_card_callback,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_card_callback_succeeded,),
)
def card_callback_ok():
    """Happy path: card-callback with optimized response."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/card-callback",
    scenario="not_found",
    input=CaseInput(
        json_body={
            "notification_id": "n-nonexistent-card",
            "response": "optimized",
            "remark": "test",
        },
    ),
    expect=ExpectError(status=404),
)
def card_callback_not_found():
    """Error path: card-callback for missing notification -> 404."""


# ===========================================================================
# 2. POST /records/offline-batch — ODPS batch ingestion (no auth)
# ===========================================================================


@endpoint_test(
    method="POST",
    path="/api/economy/governance/records/offline-batch",
    scenario="ok",
    input=CaseInput(
        json_body={
            "records": [
                {
                    "owner_id": _USER_ID,
                    "bot_id": "bot-offline-001",
                    "bot_name": "OfflineBot",
                    "dt_version": "20260705",
                    "governance_decision": "actionable",
                    "hit_dimensions": "cost_high",
                    "governance_max_priority": "P1",
                },
            ],
            "batch_id": "b-endpoint-test-ok",
            "dt_version": "20260705",
            "total_count": 1,
        },
    ),
    seed=_seed_happy_offline_batch,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"batch_id": "b-endpoint-test-ok"}},
    ),
    extra_assertions=(_assert_offline_batch_succeeded,),
)
def offline_batch_ok():
    """Happy path: offline batch ingestion."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/records/offline-batch",
    scenario="validation_error",
    input=CaseInput(
        json_body={
            "records": [],
            "batch_id": "b-empty",
            "dt_version": "20260705",
            "total_count": 0,
        },
    ),
    expect=ExpectError(status=422),
)
def offline_batch_validation_error():
    """Error path: empty records array -> 422 validation error."""
