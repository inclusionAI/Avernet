"""Endpoint coverage for governance admin router endpoints (7.5 / 6.3 / 7.3).

规整后 admin router 端点(全 body/query,零 path 参数):
  - /admin/tickets:close          关闭工单(单/多,body ticket_ids 循环 emergency_close)
  - /admin/tickets:close-all      全部关单(dispatch:cancel_pending / close_all_open)
  - /admin/tickets:deliver        按 worker_id 精准投递(不重跑状态机)
  - /admin/whitelist:delete       删除白名单条目
  - /admin/whitelist:bulk-add     批量加白
  - /admin/brake (POST)           全局制动 toggle(pause/resume)
  - /admin/brake (GET)            查询制动状态
  - /admin/records:delete         数据维护/清理
  - /admin/trigger-scan           手动触发 cron tick
  - /admin/scan-and-deliver       扫描+投递(测试工具)

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

    The admin service methods (emergency_close, close_all_open, cancel_pending)
    read from this table through TaskRecordRepository.
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

    Needed for close-all (口径对齐需 notify_log 行) 与 tickets:deliver / scan-and-deliver。
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


def _seed_tickets_close_happy(world) -> None:
    """Seed open tickets for tickets:close (单/多 emergency_close 循环)."""
    _insert_ticket(world, ticket_id="tkt-close-1", governance_status="open")
    _insert_ticket(world, ticket_id="tkt-close-2", governance_status="scheduled",
                   bot_id="bot-2", owner_id="owner-2")


def _seed_close_all_full(world) -> None:
    """Seed open ticket + pending notify for close-all (close_all_open 全量)."""
    _insert_ticket(world, ticket_id="tkt-ca-full", governance_status="open")
    _insert_notify_log(
        world, ticket_id="tkt-ca-full", bot_id="bot-ca", owner_id="owner-ca",
        notify_status="pending", governance_status="open",
    )


def _seed_close_all_unresponded(world) -> None:
    """Seed open ticket + unresponded pending notify for close-all only_unresponded."""
    _insert_ticket(world, ticket_id="tkt-ca-ur", governance_status="open")
    _insert_notify_log(
        world, ticket_id="tkt-ca-ur", bot_id="bot-ur", owner_id="owner-ur",
        notify_status="pending", governance_status="open",
    )


def _seed_tickets_deliver_happy(world) -> None:
    """Seed pending notify for a worker so tickets:deliver dry_run has content."""
    _insert_ticket(world, ticket_id="tkt-deliver-1", governance_status="open",
                   bot_id="bot-dl", owner_id="owner-dl")
    _insert_notify_log(
        world, ticket_id="tkt-deliver-1", bot_id="bot-dl", owner_id="owner-dl",
        notify_status="pending", governance_status="open",
    )


def _seed_brake_paused(world) -> None:
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


def _assert_tickets_closed(response, world) -> None:
    """Verify both tickets were closed by emergency_close 循环."""
    repo = world.get(TaskRecordRepository)
    for tid in ("tkt-close-1", "tkt-close-2"):
        ticket = repo.find_by_ticket_id(tid)
        assert ticket is not None, f"Seeded ticket {tid} should exist"
        assert ticket.governance_status == "closed", (
            f"Expected {tid} closed, got {ticket.governance_status}"
        )
        assert ticket.close_reason == "emergency_closed"


def _assert_close_all_full_closed(response, world) -> None:
    """close-all 全量(close_all_open)→ ticket 主体 CLOSED,ADMIN_CLOSED。"""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-ca-full")
    assert ticket is not None
    assert ticket.governance_status == "closed"
    assert ticket.close_reason == "admin_closed"


def _assert_close_all_unresponded_closed(response, world) -> None:
    """close-all only_unresponded(cancel_pending)→ ticket 主体 CLOSED,EMERGENCY_CLOSED。"""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-ca-ur")
    assert ticket is not None
    assert ticket.governance_status == "closed"
    assert ticket.close_reason == "emergency_closed"


def _assert_whitelist_deleted(response, world) -> None:
    """Verify whitelist entries were actually deleted."""
    body = response.json()
    assert body["success"] is True
    data = body.get("data", [])
    assert len(data) >= 1, f"Expected at least 1 deletion result, got {data}"


def _assert_brake_paused(response, world) -> None:
    """GET /admin/brake 返回 paused=True(seeded)。"""
    body = response.json()
    assert body["success"] is True
    assert body["data"]["paused"] is True


# ---------------------------------------------------------------------------
# 1. /admin/tickets:close (单/多,emergency_close 循环)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:close",
    scenario="ok_multi",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "reason": "urgent",
            "ticket_ids": ["tkt-close-1", "tkt-close-2"],
        },
    ),
    seed=_seed_tickets_close_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_tickets_closed,),
)
def tickets_close_multi_ok():
    """Happy path: close multiple tickets via emergency_close 循环."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:close",
    scenario="not_found_returns_outcome",
    input=CaseInput(
        headers=_USER_HEADER,
        # 批量循环不整体 raise;not-found 返回 200 + outcome 含 error_code=NOT_FOUND
        json_body={
            "reason": "test",
            "ticket_ids": ["tkt-nonexistent-999"],
        },
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def tickets_close_not_found_returns_outcome():
    """批量场景:不存在的 ticket → 200 + outcome 含 error(不整体 404)."""


# ---------------------------------------------------------------------------
# 2. /admin/tickets:close-all (dispatch:close_all_open / cancel_pending)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:close-all",
    scenario="ok_full_close_all_open",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"reason": "bulk close"},
    ),
    seed=_seed_close_all_full,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_close_all_full_closed,),
)
def tickets_close_all_full_ok():
    """Happy path: close-all 全量 → close_all_open(ADMIN_CLOSED)."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:close-all",
    scenario="ok_only_unresponded_cancel_pending",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"reason": "cancel unresponded", "only_unresponded": True},
    ),
    seed=_seed_close_all_unresponded,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_close_all_unresponded_closed,),
)
def tickets_close_all_only_unresponded_ok():
    """Happy path: close-all only_unresponded → cancel_pending(EMERGENCY_CLOSED)."""


# ---------------------------------------------------------------------------
# 3. /admin/tickets:deliver (按 worker 精准投递,不重跑状态机)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:deliver",
    scenario="ok_dry_run",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "worker_id": "owner-dl:bot-dl",
            "dry_run": True,
        },
    ),
    seed=_seed_tickets_deliver_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def tickets_deliver_dry_run_ok():
    """Happy path: deliver pending notifies for a worker (dry_run)."""


# ---------------------------------------------------------------------------
# 4. /admin/whitelist:delete
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/whitelist:delete",
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
    path="/api/economy/governance/admin/whitelist:delete",
    scenario="error",
    input=CaseInput(
        headers=_USER_HEADER,
        # No bot_owner_pairs -> Pydantic min_length=1 validates with 422
        json_body={"bot_owner_pairs": [], "reason": "test"},
    ),
    expect=ExpectError(status=422),
)
def whitelist_delete_error():
    """Error path: whitelist delete with empty pairs -> 422."""


# ---------------------------------------------------------------------------
# 5. /admin/whitelist:bulk-add
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/whitelist:bulk-add",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "bot_ids": ["bot-bulk-1", "bot-bulk-2"],
            "reason": "bulk test",
        },
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def whitelist_bulk_add_ok():
    """Happy path: bulk whitelist bots."""


# ---------------------------------------------------------------------------
# 6. /admin/brake (POST toggle)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/brake",
    scenario="ok_pause",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"enabled": True, "reason": "test pause"},
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True, "message": "Paused"}),
)
def brake_toggle_pause_ok():
    """Happy path: brake toggle enabled=true → pause."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/brake",
    scenario="ok_resume",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"enabled": False, "reason": "test resume"},
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True, "message": "Resumed"}),
)
def brake_toggle_resume_ok():
    """Happy path: brake toggle enabled=false → resume."""


# ---------------------------------------------------------------------------
# 7. /admin/brake (GET state)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/brake",
    scenario="ok_unpaused",
    input=CaseInput(headers=_USER_HEADER),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def brake_get_ok():
    """Happy path: get brake state (unpaused)."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/brake",
    scenario="paused",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_brake_paused,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"paused": True}},
    ),
    extra_assertions=(_assert_brake_paused,),
)
def brake_get_paused():
    """Alternate path: get brake state when paused."""


# ---------------------------------------------------------------------------
# 8. /admin/trigger-scan
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
        query_params={"dry_run": "not_a_bool"},
    ),
    expect=ExpectError(status=422),
)
def trigger_scan_error():
    """Error path: trigger scan with invalid dry_run -> 422."""


# ---------------------------------------------------------------------------
# 9. /admin/scan-and-deliver (测试工具,不动)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/scan-and-deliver",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
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
        query_params={"override_recipient": "not_numeric"},
    ),
    expect=ExpectError(status=422),
)
def scan_and_deliver_error():
    """Error path: scan-and-deliver with invalid recipient -> 422."""


# ---------------------------------------------------------------------------
# 10. /admin/brake (GET) — no auth
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/brake",
    scenario="no_auth",
    input=CaseInput(),  # no x-user-id header → LocalAuth raises Unauthorized
    expect=ExpectError(status=401),
)
def brake_get_no_auth():
    """Error path: get brake state without auth → 401."""


# ---------------------------------------------------------------------------
# 11. Error cases for new endpoints (422 validation — coverage gate happy+error)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:close",
    scenario="error_empty_ticket_ids",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"reason": "test", "ticket_ids": []},
    ),
    expect=ExpectError(status=422),
)
def tickets_close_error_empty_ids():
    """Error path: empty ticket_ids -> 422 (Pydantic min_length=1)."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:close-all",
    scenario="error_missing_reason",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"only_unresponded": True},
    ),
    expect=ExpectError(status=422),
)
def tickets_close_all_error_missing_reason():
    """Error path: missing required reason -> 422."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:deliver",
    scenario="error_missing_worker_id",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"dry_run": True},
    ),
    expect=ExpectError(status=422),
)
def tickets_deliver_error_missing_worker_id():
    """Error path: missing required worker_id -> 422."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/whitelist:bulk-add",
    scenario="error_empty_bot_ids",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"bot_ids": [], "reason": "test"},
    ),
    expect=ExpectError(status=422),
)
def whitelist_bulk_add_error_empty_bot_ids():
    """Error path: empty bot_ids -> 422 (Pydantic min_length=1)."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/brake",
    scenario="error_missing_enabled",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"reason": "test"},
    ),
    expect=ExpectError(status=422),
)
def brake_toggle_error_missing_enabled():
    """Error path: missing required enabled field -> 422."""
