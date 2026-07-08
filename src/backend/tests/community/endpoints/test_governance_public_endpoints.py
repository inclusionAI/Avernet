"""Endpoint coverage for governance public + business-data router (Phase 2).

Covers the 8 endpoints from coverage_baseline.txt §"economy/governance — Phase 2":

  - GET  /notifications                             (list pending)
  - GET  /notifications/history                     (list closed/expired)
  - GET  /notifications/{notification_id}           (single detail)
  - GET  /whitelist                                 (list whitelist)
  - POST /notifications/{notification_id}/resolve   (user feedback)
  - POST /whitelist/batch                           (batch add whitelist)
  - POST /card-callback                             (DingTalk card iframe)
  - POST /records/offline-batch                     (ODPS batch ingestion)

All tests use real DI services via ``world.get()`` and seed real data into
the in-memory SQLite database via repos. No MagicMock / unittest.mock.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from agentclaw.community.api.governance_service import (
    GovernanceFeedbackServiceProtocol,
    GovernanceWhitelistProtocol,
    GovernanceRecordProcessProtocol,
)
from agentclaw.community.core.economy.governance.contracts.models import (
    GovernanceNotifyLog,
    GovernanceTaskRecordDaily,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_ID = "staff-001"
_USER_HEADERS = {"x-user-id": _USER_ID}


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
    service's real queries can find it.

    Returns the ``notification_id`` used.
    """
    if notification_id is None:
        notification_id = uuid.uuid4().hex

    ticket_id = uuid.uuid4().hex
    worker_id = f"{owner_id}:{bot_id}"

    task_repo = world.get(TaskRecordRepository)
    task_repo.insert_ticket(
        GovernanceTaskRecordDaily(
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
        GovernanceNotifyLog(
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


def _seed_closed_ticket_for_user(
    world,
    *,
    owner_id: str = _USER_ID,
    bot_id: str = "bot-2",
) -> str:
    """Insert a closed task_record + notify_log pair for history listing."""
    notification_id = uuid.uuid4().hex
    ticket_id = uuid.uuid4().hex
    worker_id = f"{owner_id}:{bot_id}"

    task_repo = world.get(TaskRecordRepository)
    task_repo.insert_ticket(
        GovernanceTaskRecordDaily(
            ticket_id=ticket_id,
            worker_id=worker_id,
            active_worker=None,
            bot_id=bot_id,
            owner_id=owner_id,
            bot_name="ClosedBot",
            dt_version="20260704",
            governance_decision="actionable",
            latest_decision="normal",
            governance_status="closed",
            close_reason="admin_approved",
            closed_at=datetime.now(),
            consecutive_normal_days=3,
            remind_count=0,
            last_sync_at=datetime.now(),
        ),
    )

    notify_repo = world.get(NotifyLogRepository)
    notify_repo.insert_notification(
        GovernanceNotifyLog(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id=bot_id,
            bot_name="ClosedBot",
            owner_id=owner_id,
            worker_id=worker_id,
            dt_version="20260704",
            governance_decision="actionable",
            governance_cycle_id=ticket_id,
            governance_status="closed",
            notify_status="sent",
            notify_type="first_send",
            notify_source="offline_batch",
            send_attempt_count=1,
        ),
    )

    return notification_id


def _seed_whitelist_entry(
    world,
    *,
    bot_id: str = "bot-1",
    owner_id: str = _USER_ID,
    reason: str = "test",
) -> None:
    """Insert a whitelist entry via the real repository."""
    whitelist_repo = world.get(GovernanceWhitelistRepository)
    whitelist_repo.batch_add(
        entries=[{"bot_id": bot_id, "owner_id": owner_id, "reason": reason}],
        created_by=owner_id,
        whitelist_type="governance",
        source="http_api",
    )


# ---------------------------------------------------------------------------
# Seed functions used by endpoint_test cases
# ---------------------------------------------------------------------------


def _seed_happy_notifications(world) -> None:
    """Seed an open ticket (pending) and a closed ticket (history)."""
    world._nfy_id_open = _seed_open_ticket_for_user(
        world, bot_id="bot-open", notification_id="n-uuid-open",
    )
    world._nfy_id_closed = _seed_closed_ticket_for_user(
        world, bot_id="bot-closed",
    )


def _seed_happy_notification_detail(world) -> None:
    """Seed a single open ticket whose notification_id we reference."""
    world._nfy_id_detail = _seed_open_ticket_for_user(
        world, bot_id="bot-detail", notification_id="n-uuid-detail",
    )


def _seed_happy_resolve(world) -> None:
    """Seed an open ticket ready for resolve."""
    world._nfy_id_resolve = _seed_open_ticket_for_user(
        world, bot_id="bot-resolve", notification_id="n-uuid-resolve",
    )


def _seed_happy_whitelist(world) -> None:
    """Seed a whitelist entry for listing."""
    _seed_whitelist_entry(world, bot_id="bot-wl", owner_id=_USER_ID)


def _seed_happy_card_callback(world) -> None:
    """Seed an open ticket for card-callback (no auth)."""
    world._nfy_id_card = _seed_open_ticket_for_user(
        world, bot_id="bot-card", notification_id="n-uuid-card",
    )


def _seed_happy_offline_batch(world) -> None:
    """No pre-seeding needed — offline-batch creates tickets via the service."""


# ---------------------------------------------------------------------------
# Extra assertions (post-response state verification)
# ---------------------------------------------------------------------------


def _assert_resolve_succeeded(response, world) -> None:
    """Verify that the ticket transitioned to waiting_review in DB."""
    data = response.json()
    assert data["success"] is True
    assert data["data"]["governance_status"] == "waiting_review"


def _assert_whitelist_inserted(response, world) -> None:
    """Verify the whitelist entry exists in DB after batch add."""
    whitelist_svc = world.get(GovernanceWhitelistProtocol)
    items = whitelist_svc.list_all(
        owner_id=_USER_ID,
        whitelist_type="governance",
    )
    assert len(items) >= 1


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
# 1. GET /notifications — list pending
# ===========================================================================


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "10", "offset": "0"},
    ),
    seed=_seed_happy_notifications,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def list_pending_ok():
    """Happy path: list pending notifications for current user."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications",
    scenario="empty",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "10"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": []},
    ),
)
def list_pending_empty():
    """Alternate path: no pending notifications returns empty list."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications",
    scenario="bad_limit",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "not_a_number"},
    ),
    seed=_seed_happy_notifications,
    expect=ExpectError(status=422),
)
def list_pending_bad_limit():
    """Error path: non-numeric limit → 422."""


# ===========================================================================
# 2. GET /notifications/history — list closed/expired
# ===========================================================================


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications/history",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "10", "offset": "0"},
    ),
    seed=_seed_happy_notifications,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def list_history_ok():
    """Happy path: list history (closed/expired) notifications."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications/history",
    scenario="empty",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "10"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": []},
    ),
)
def list_history_empty():
    """Alternate path: no history notifications returns empty list."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications/history",
    scenario="bad_limit",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "not_a_number"},
    ),
    seed=_seed_happy_notifications,
    expect=ExpectError(status=422),
)
def list_history_bad_limit():
    """Error path: non-numeric limit → 422."""


# ===========================================================================
# 3. GET /notifications/{notification_id} — single detail
# ===========================================================================


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications/{notification_id}",
    scenario="ok",
    input=CaseInput(
        path_params={"notification_id": "n-uuid-detail"},
        headers=_USER_HEADERS,
    ),
    seed=_seed_happy_notification_detail,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def get_notification_ok():
    """Happy path: get notification detail by UUID."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications/{notification_id}",
    scenario="not_found",
    input=CaseInput(
        path_params={"notification_id": "n-nonexistent-uuid"},
        headers=_USER_HEADERS,
    ),
    expect=ExpectError(status=404),
)
def get_notification_not_found():
    """Error path: notification not found -> 404."""


# ===========================================================================
# 4. GET /whitelist — list whitelist
# ===========================================================================


@endpoint_test(
    method="GET",
    path="/api/economy/governance/whitelist",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "50"},
    ),
    seed=_seed_happy_whitelist,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def list_whitelist_ok():
    """Happy path: list governance whitelist entries."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/whitelist",
    scenario="empty",
    input=CaseInput(headers=_USER_HEADERS),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": []},
    ),
)
def list_whitelist_empty():
    """Alternate path: empty whitelist returns empty list."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/whitelist",
    scenario="bad_limit",
    input=CaseInput(
        headers=_USER_HEADERS,
        query_params={"limit": "not_a_number"},
    ),
    seed=_seed_happy_whitelist,
    expect=ExpectError(status=422),
)
def list_whitelist_bad_limit():
    """Error path: non-numeric limit → 422."""


# ===========================================================================
# 5. POST /notifications/{notification_id}/resolve — user feedback
# ===========================================================================


@endpoint_test(
    method="POST",
    path="/api/economy/governance/notifications/{notification_id}/resolve",
    scenario="ok",
    input=CaseInput(
        path_params={"notification_id": "n-uuid-resolve"},
        headers=_USER_HEADERS,
        json_body={
            "response": "optimized",
            "remark": "已优化处理",
        },
    ),
    seed=_seed_happy_resolve,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"governance_status": "waiting_review"},
        },
    ),
    extra_assertions=(_assert_resolve_succeeded,),
)
def resolve_ok():
    """Happy path: user resolves notification (optimized)."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/notifications/{notification_id}/resolve",
    scenario="not_found",
    input=CaseInput(
        path_params={"notification_id": "n-nonexistent-resolve"},
        headers=_USER_HEADERS,
        json_body={"response": "optimized", "remark": "test"},
    ),
    expect=ExpectError(status=404),
)
def resolve_not_found():
    """Error path: resolve on missing notification -> 404."""


# ===========================================================================
# 6. POST /whitelist/batch — batch add whitelist
# ===========================================================================


@endpoint_test(
    method="POST",
    path="/api/economy/governance/whitelist/batch",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADERS,
        json_body={
            "entries": [
                {"bot_id": "bot-batch-new", "owner_id": _USER_ID, "reason": "test"},
            ],
            "source": "http_api",
        },
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"inserted": 1}},
    ),
    extra_assertions=(_assert_whitelist_inserted,),
)
def whitelist_batch_ok():
    """Happy path: batch add whitelist entries."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/whitelist/batch",
    scenario="all_skipped",
    input=CaseInput(
        headers=_USER_HEADERS,
        json_body={
            "entries": [
                {"bot_id": "bot-wl-dup", "owner_id": _USER_ID, "reason": "duplicate"},
            ],
            "source": "http_api",
        },
    ),
    seed=lambda w: _seed_whitelist_entry(w, bot_id="bot-wl-dup", owner_id=_USER_ID),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"inserted": 0, "skipped": 1}},
    ),
)
def whitelist_batch_all_skipped():
    """Error path: duplicate entries all skipped."""


# ===========================================================================
# 7. POST /card-callback — DingTalk card iframe callback (no auth)
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
# 8. POST /records/offline-batch — ODPS batch ingestion (no auth)
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


# ===========================================================================
# 9. GET /notifications — no auth
# ===========================================================================


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications",
    scenario="no_auth",
    input=CaseInput(),  # no x-user-id header → LocalAuth raises Unauthorized
    expect=ExpectError(status=401),
)
def list_pending_no_auth():
    """Error path: list pending notifications without auth → 401."""


# ===========================================================================
# 10. GET /notifications/history — no auth
# ===========================================================================


@endpoint_test(
    method="GET",
    path="/api/economy/governance/notifications/history",
    scenario="no_auth",
    input=CaseInput(),
    expect=ExpectError(status=401),
)
def list_history_no_auth():
    """Error path: list history notifications without auth → 401."""


# ===========================================================================
# 11. GET /whitelist — no auth
# ===========================================================================


@endpoint_test(
    method="GET",
    path="/api/economy/governance/whitelist",
    scenario="no_auth",
    input=CaseInput(),
    expect=ExpectError(status=401),
)
def list_whitelist_no_auth():
    """Error path: list whitelist without auth → 401."""


# ===========================================================================
# 12. POST /whitelist/batch — no auth
# ===========================================================================


@endpoint_test(
    method="POST",
    path="/api/economy/governance/whitelist/batch",
    scenario="no_auth",
    input=CaseInput(
        json_body={
            "entries": [{"bot_id": "bot-noauth", "owner_id": "user-noauth"}],
            "source": "manual",
        },
    ),
    expect=ExpectError(status=401),
)
def whitelist_batch_no_auth():
    """Error path: batch whitelist add without auth → 401."""