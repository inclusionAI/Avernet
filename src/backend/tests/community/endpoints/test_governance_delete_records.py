"""Endpoint coverage for POST /admin/records:delete (emergency records deletion).

Covers error-path cases flagged in Phase 2 coverage:
  - no auth → 401
  - missing filter fields → 400
  - invalid table name → 400

Also includes a happy-path case for completeness:
  - dry_run delete on seeded notify_log rows → 200
"""
from __future__ import annotations

import uuid
from datetime import datetime

from agentclaw.community.core.economy.governance.repositories.orm import (
    GovernanceNotificationOrm,
    GovernanceTicketOrm,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_HEADER = {"x-user-id": "88888"}


# ---------------------------------------------------------------------------
# Seed helpers — insert real data via repos
# ---------------------------------------------------------------------------


def _seed_notify_log_for_delete(world) -> None:
    """Insert a notify_log row so dry-run delete has something to match."""
    repo = world.get(NotifyLogRepository)
    notification_id = f"n-del-{uuid.uuid4().hex[:12]}"
    ticket_id = f"t-del-{uuid.uuid4().hex[:8]}"
    worker_id = "owner-del:bot-del"

    # Also need a task_record row (FK-like logical dependency)
    task_repo = world.get(TaskRecordRepository)
    task_repo.insert_ticket(
        GovernanceTicketOrm(
            ticket_id=ticket_id,
            worker_id=worker_id,
            active_worker=worker_id,
            bot_id="bot-del",
            owner_id="owner-del",
            bot_name="DelBot",
            dt_version="20260705",
            governance_decision="actionable",
            governance_status="open",
            latest_decision="actionable",
            consecutive_normal_days=0,
            remind_count=0,
            last_sync_at=datetime.now(),
        ),
    )

    repo.insert_notification(
        GovernanceNotificationOrm(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id="bot-del",
            bot_name="DelBot",
            owner_id="owner-del",
            worker_id=worker_id,
            dt_version="20260705",
            governance_decision="actionable",
            governance_cycle_id="cycle-del",
            governance_status="open",
            notify_status="pending",
            notify_type="first_send",
            notify_source="offline_batch",
            send_attempt_count=0,
        ),
    )


# ---------------------------------------------------------------------------
# 1. POST /admin/records:delete — happy path (dry_run)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/records:delete",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "table": "notify_log",
            "dt_versions": ["20260705"],
            "dry_run": True,
            "reason": "test dry-run",
        },
    ),
    seed=_seed_notify_log_for_delete,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def delete_records_ok():
    """Happy path: dry-run delete on notify_log by dt_version."""


# ---------------------------------------------------------------------------
# 2. POST /admin/records:delete — no auth → 401
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/records:delete",
    scenario="no_auth",
    input=CaseInput(
        json_body={
            "table": "notify_log",
            "dt_versions": ["20260705"],
            "dry_run": True,
            "reason": "no-auth test",
        },
    ),
    expect=ExpectError(status=401),
)
def delete_records_no_auth():
    """Error path: delete records without auth → 401."""


# ---------------------------------------------------------------------------
# 3. POST /admin/records:delete — missing filter fields → 400
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/records:delete",
    scenario="missing_filters",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "table": "record_daily",
            "dry_run": True,
            "reason": "missing filter fields",
        },
    ),
    expect=ExpectError(status=400),
)
def delete_records_missing_filters():
    """Error path: no dt_versions / ids / notification_ids → 400."""


# ---------------------------------------------------------------------------
# 4. POST /admin/records:delete — invalid table name → 400
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/records:delete",
    scenario="invalid_table",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "table": "bad_table",
            "ids": [1],
            "dry_run": True,
            "reason": "invalid table",
        },
    ),
    expect=ExpectError(status=400),
)
def delete_records_invalid_table():
    """Error path: table name not record_daily/notify_log → 400."""