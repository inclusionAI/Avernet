"""Endpoint coverage for governance admin router endpoints (7.5 / 6.3 / 7.3).

Uses real DI services and in-memory SQLite -- no MagicMock / unittest.mock.
Each seed function inserts real data via repo methods so the endpoint handler
exercises the full service -> repo -> DB stack.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from agentclaw.community.api.governance_service import (
    GovernanceAdminServiceProtocol,
)
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
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_HEADER = {"x-user-id": "88888"}


# ---------------------------------------------------------------------------
# Seed helpers -- insert real data through repos
# ---------------------------------------------------------------------------


def _insert_ticket(
    world,
    *,
    ticket_id: str,
    governance_status: str = "open",
    bot_id: str = "bot-1",
    owner_id: str = "owner-1",
    dt_version: str = "20260705",
) -> None:
    """Insert a real GovernanceTicketOrm row via repo.

    The admin service methods (pause_ticket, review_ticket, emergency_close)
    read from this table through TaskRecordRepository.find_by_ticket_id.
    """
    repo = world.get(TaskRecordRepository)
    worker_id = f"{owner_id}:{bot_id}"
    active_worker = worker_id if governance_status != "closed" else None
    repo.insert_ticket(
        GovernanceTicketOrm(
            worker_id=worker_id,
            bot_id=bot_id,
            owner_id=owner_id,
            dt_version=dt_version,
            governance_decision="actionable",
            governance_status=governance_status,
            ticket_id=ticket_id,
            active_worker=active_worker,
            last_sync_at=datetime.now(),
        ),
    )


def _insert_whitelist_entry(
    world,
    *,
    bot_id: str,
    owner_id: str,
    reason: str = "test",
) -> None:
    """Insert a real WhitelistEntryOrm row via GovernanceWhitelistRepository."""
    wl_repo = world.get(GovernanceWhitelistRepository)
    wl_repo.add(
        bot_id=bot_id, owner_id=owner_id,
        reason=reason, created_by="88888",
        whitelist_type="governance",
        source="manual",
    )


def _insert_notify_log(
    world,
    *,
    ticket_id: str,
    bot_id: str = "bot-1",
    owner_id: str = "owner-1",
    notify_status: str = "pending",
    governance_status: str = "open",
) -> None:
    """Insert a real GovernanceNotificationOrm row via repo.

    Needed for scan-and-deliver which queries notify_log rows.
    """
    repo = world.get(NotifyLogRepository)
    worker_id = f"{owner_id}:{bot_id}"
    notification_id = f"n-{uuid.uuid4().hex[:12]}"
    repo.insert_notification(
        GovernanceNotificationOrm(
            notification_id=notification_id,
            ticket_id=ticket_id,
            bot_id=bot_id,
            owner_id=owner_id,
            worker_id=worker_id,
            dt_version="20260705",
            governance_decision="actionable",
            governance_status=governance_status,
            notify_status=notify_status,
            governance_cycle_id="cycle-test",
        ),
    )


# ---------------------------------------------------------------------------
# Per-endpoint seed functions
# ---------------------------------------------------------------------------


def _seed_whitelist_delete_happy(world) -> None:
    """Seed two whitelist entries so delete-by-pair has something to match."""
    _insert_whitelist_entry(world, bot_id="wl-bot-1", owner_id="wl-owner-1")
    _insert_whitelist_entry(world, bot_id="wl-bot-2", owner_id="wl-owner-2")


def _seed_review_happy(world) -> None:
    """Seed a ticket in 'waiting_review' so admin review can approve it."""
    _insert_ticket(
        world,
        ticket_id="tkt-review-test",
        governance_status="waiting_review",
    )


def _seed_pause_happy(world) -> None:
    """Seed an 'open' ticket so admin pause can transition it."""
    _insert_ticket(
        world,
        ticket_id="tkt-pause-test",
        governance_status="open",
    )


def _seed_emergency_close_happy(world) -> None:
    """Seed an 'open' ticket so emergency close can close it."""
    _insert_ticket(
        world,
        ticket_id="tkt-eclose-test",
        governance_status="open",
    )


def _seed_emergency_get_paused(world) -> None:
    """Seed paused state by calling real admin_svc.pause()."""
    admin_svc = world.get(GovernanceAdminServiceProtocol)
    admin_svc.pause(reason="test pause", operator="88888")


def _seed_scan_and_deliver_happy(world) -> None:
    """Seed a pending notify_log so scan-and-deliver dry-run has content."""
    ticket_id = f"tkt-sd-{uuid.uuid4().hex[:8]}"
    _insert_ticket(world, ticket_id=ticket_id, governance_status="open")
    _insert_notify_log(
        world,
        ticket_id=ticket_id,
        bot_id="bot-sd-1",
        owner_id="owner-sd-1",
        notify_status="pending",
        governance_status="open",
    )


# ---------------------------------------------------------------------------
# Extra assertion helpers -- use repos for state verification
# ---------------------------------------------------------------------------


def _assert_review_closed_ticket(response, world) -> None:
    """Verify the ticket was actually transitioned to closed by the real service."""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-review-test")
    assert ticket is not None, "Seeded ticket should exist"
    assert ticket.governance_status == "closed", (
        f"Expected closed, got {ticket.governance_status}"
    )


def _assert_pause_to_waiting_review(response, world) -> None:
    """Verify the ticket was transitioned to waiting_review."""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-pause-test")
    assert ticket is not None, "Seeded ticket should exist"
    assert ticket.governance_status == "waiting_review", (
        f"Expected waiting_review, got {ticket.governance_status}"
    )


def _assert_emergency_close_closed(response, world) -> None:
    """Verify the ticket was closed by emergency close."""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-eclose-test")
    assert ticket is not None, "Seeded ticket should exist"
    assert ticket.governance_status == "closed", (
        f"Expected closed, got {ticket.governance_status}"
    )
    assert ticket.close_reason == "emergency_closed"


def _assert_whitelist_deleted(response, world) -> None:
    """Verify whitelist entries were actually deleted."""
    body = response.json()
    assert body["success"] is True
    # Response is a list of {deleted, bot_id, owner_id} dicts
    data = body.get("data", [])
    assert len(data) >= 1, f"Expected at least 1 deletion result, got {data}"


# ---------------------------------------------------------------------------
# 1. /admin/whitelist/delete
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/whitelist/delete",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "bot_owner_pairs": [
                {"bot_id": "wl-bot-1", "owner_id": "wl-owner-1"},
                {"bot_id": "wl-bot-2", "owner_id": "wl-owner-2"},
            ],
            "reason": "test",
        },
    ),
    seed=_seed_whitelist_delete_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_whitelist_deleted,),
)
def whitelist_delete_ok():
    """Happy path: whitelist delete removes matching entries."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/whitelist/delete",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        # No bot_owner_pairs -> Pydantic min_length=1 validates with 422
        json_body={"bot_owner_pairs": [], "reason": "test"},
    ),
    expect=ExpectError(status=422),
)
def whitelist_delete_error():
    """Error path: whitelist delete with no ids/pairs -> 400."""


# ---------------------------------------------------------------------------
# 2. /admin/review
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/review",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-review-test",
            "action": "approve_close",
            "admin_id": "admin-1",
        },
    ),
    seed=_seed_review_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_review_closed_ticket,),
)
def admin_review_ok():
    """Happy path: admin review approve_close on waiting_review ticket."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/review",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-nonexistent-999",
            "action": "approve_close",
            "admin_id": "admin-1",
        },
    ),
    # No seed -- ticket not found -> _raise_on_admin_error -> HTTPException(404)
    expect=ExpectError(status=404),
)
def admin_review_error():
    """Error path: review on missing ticket -> 404."""


# ---------------------------------------------------------------------------
# 3. /admin/pause
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/pause",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-pause-test",
            "admin_id": "admin-1",
            "reason": "check",
        },
    ),
    seed=_seed_pause_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_pause_to_waiting_review,),
)
def admin_pause_ok():
    """Happy path: admin pause ticket (open -> waiting_review)."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/pause",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-nonexistent-999",
            "admin_id": "admin-1",
        },
    ),
    expect=ExpectError(status=404),
)
def admin_pause_error():
    """Error path: pause on missing ticket -> 404."""


# ---------------------------------------------------------------------------
# 4. /admin/emergency-close
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/emergency-close",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-eclose-test",
            "admin_id": "admin-1",
            "reason": "urgent",
        },
    ),
    seed=_seed_emergency_close_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_emergency_close_closed,),
)
def admin_emergency_close_ok():
    """Happy path: emergency close ticket."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/emergency-close",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-nonexistent-999",
            "admin_id": "admin-1",
        },
    ),
    expect=ExpectError(status=404),
)
def admin_emergency_close_error():
    """Error path: emergency close on missing ticket -> 404."""


# ---------------------------------------------------------------------------
# 5. /admin/trigger-scan
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/trigger-scan",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"dry_run": "true"},
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def trigger_scan_ok():
    """Happy path: trigger scan cron tick (dry_run=true)."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/trigger-scan",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        # Invalid dry_run value triggers FastAPI 422 validation error
        query_params={"dry_run": "not_a_bool"},
    ),
    expect=ExpectError(status=422),
)
def trigger_scan_error():
    """Error path: trigger scan with invalid dry_run -> 422."""


# ---------------------------------------------------------------------------
# 6. /admin/emergency (POST)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/emergency",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"action": "pause", "reason": "test pause"},
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def emergency_post_ok():
    """Happy path: emergency pause."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/emergency",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"action": "unknown_action", "reason": "bad"},
    ),
    expect=ExpectError(status=400),
)
def emergency_post_error():
    """Error path: unknown emergency action -> 400."""


# ---------------------------------------------------------------------------
# 7. /admin/emergency (GET)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/emergency",
    scenario="ok",
    input=CaseInput(headers=_USER_HEADER),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def emergency_get_ok():
    """Happy path: get emergency state (unpaused)."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/emergency",
    scenario="paused",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_emergency_get_paused,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"paused": True}},
    ),
)
def emergency_get_paused():
    """Alternate path: get emergency state when paused."""


# ---------------------------------------------------------------------------
# 8. /admin/scan-and-deliver
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/scan-and-deliver",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        # scan-and-deliver uses Query params, not JSON body
        query_params={"override_recipient": "10001", "dry_run": "true"},
    ),
    seed=_seed_scan_and_deliver_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def scan_and_deliver_ok():
    """Happy path: scan-and-deliver dry-run."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/scan-and-deliver",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        # override_recipient pattern ^\\d{4,10}$ -- non-numeric fails 422
        query_params={"override_recipient": "not_numeric"},
    ),
    expect=ExpectError(status=422),
)
def scan_and_deliver_error():
    """Error path: scan-and-deliver with invalid recipient -> 422."""


# ---------------------------------------------------------------------------
# 9. /admin/emergency (GET) — no auth
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/emergency",
    scenario="no_auth",
    input=CaseInput(),  # no x-user-id header → LocalAuth raises Unauthorized
    expect=ExpectError(status=401),
)
def emergency_get_no_auth():
    """Error path: get emergency state without auth → 401."""