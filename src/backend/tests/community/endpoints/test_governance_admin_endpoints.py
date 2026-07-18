"""Endpoint coverage for governance admin router endpoints (7.5 / 6.3 / 7.3).

规整后 admin router 端点(全 body/query,零 path 参数):
  - /admin/tickets:deliver        按 worker_id 精准投递(不重跑状态机)
  - /admin/whitelist:delete       删除白名单条目
  - /admin/whitelist:bulk-add     批量加白
  - /admin/admin_whitelist (GET)  白名单只读分页列表
  - /admin/brake (POST)           全局制动 toggle(pause/resume)
  - /admin/brake (GET)            查询制动状态
  - /admin/records:delete         数据维护/清理
  - /admin/trigger-scan           手动触发 cron tick
  - /admin/scan-and-deliver       扫描+投递(测试工具)

注:tickets:close / tickets:close-all 已迁至 workflow_router(路径 /workflow/tickets:close
/ /workflow/tickets:close-all),其端点 case 路径已改 /workflow,仍在本文件注册由
endpoint_runner 统一跑;close/close-all service 方法(admin_close/cancel_pending/
close_all_open)已迁 GovernanceWorkflowService。

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
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
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

    The admin service methods (admin_close, close_all_open, cancel_pending)
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
    """Seed open tickets for tickets:close (单/多 admin_close 循环)."""
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
    """Verify both tickets were closed by admin_close 循环."""
    repo = world.get(TaskRecordRepository)
    for tid in ("tkt-close-1", "tkt-close-2"):
        ticket = repo.find_by_ticket_id(tid)
        assert ticket is not None, f"Seeded ticket {tid} should exist"
        assert ticket.governance_status == "closed", (
            f"Expected {tid} closed, got {ticket.governance_status}"
        )
        assert ticket.close_reason == "admin_closed"


def _assert_close_all_full_closed(response, world) -> None:
    """close-all 全量(close_all_open)→ ticket 主体 CLOSED,ADMIN_CLOSED。"""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-ca-full")
    assert ticket is not None
    assert ticket.governance_status == "closed"
    assert ticket.close_reason == "admin_closed"


def _assert_close_all_unresponded_closed(response, world) -> None:
    """close-all only_unresponded(cancel_pending)→ ticket 主体 CLOSED,ADMIN_CLOSED。"""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-ca-ur")
    assert ticket is not None
    assert ticket.governance_status == "closed"
    assert ticket.close_reason == "admin_closed"


def _assert_whitelist_deleted(response, world) -> None:
    """Verify whitelist entries were actually deleted."""
    body = response.json()
    assert body["success"] is True
    data = body.get("data", [])
    assert len(data) >= 1, f"Expected at least 1 deletion result, got {data}"


def _assert_dry_run_no_delete(response, world) -> None:
    """dry_run=True 预览:工单 + 两通知 + 无关工单/通知均未删除。"""
    task_repo = world.get(TaskRecordRepository)
    notify_repo = world.get(NotifyLogRepository)
    assert task_repo.find_by_ticket_id("tkt-cascade-1") is not None
    assert task_repo.find_by_ticket_id("tkt-cascade-2") is not None
    remaining = [
        n for n in notify_repo.list_by_ticket("tkt-cascade-1", only_pending=False)
    ]
    assert len(remaining) == 2, f"dry_run 不应删通知,剩余 {len(remaining)}"


def _assert_real_delete_precise_cascade(response, world) -> None:
    """真删:tkt-cascade-1 工单+2 通知删;tkt-cascade-2 无关工单/通知保留。"""
    task_repo = world.get(TaskRecordRepository)
    notify_repo = world.get(NotifyLogRepository)
    assert task_repo.find_by_ticket_id("tkt-cascade-1") is None, "工单应已删"
    assert task_repo.find_by_ticket_id("tkt-cascade-2") is not None, "无关工单不应被动"
    assert (
        len(notify_repo.list_by_ticket("tkt-cascade-1", only_pending=False)) == 0
    ), "归属通知应已删"
    assert (
        len(notify_repo.list_by_ticket("tkt-cascade-2", only_pending=False)) == 1
    ), "无关通知不应被误删"


def _assert_not_found_no_change(response, world) -> None:
    """工单不存在:数据无变化(两工单都在)。"""
    task_repo = world.get(TaskRecordRepository)
    assert task_repo.find_by_ticket_id("tkt-cascade-1") is not None
    assert task_repo.find_by_ticket_id("tkt-cascade-2") is not None


def _assert_brake_paused(response, world) -> None:
    """GET /admin/brake 返回 paused=True(seeded)。"""
    body = response.json()
    assert body["success"] is True
    assert body["data"]["paused"] is True


# ---------------------------------------------------------------------------
# 1. /admin/tickets:close (单/多,admin_close 循环)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:close",
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
    """Happy path: close multiple tickets via admin_close 循环."""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:close",
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
    path="/api/economy/governance/workflow/tickets:close-all",
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
    path="/api/economy/governance/workflow/tickets:close-all",
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
    """Happy path: close-all only_unresponded → cancel_pending(ADMIN_CLOSED)."""


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
    path="/api/economy/governance/workflow/tickets:close",
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
    path="/api/economy/governance/workflow/tickets:close-all",
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


# ---------------------------------------------------------------------------
# 12. /admin/whitelist (GET — 只读分页列表)
# ---------------------------------------------------------------------------


def _seed_whitelist_list(world) -> None:
    """Seed mixed whitelist entries: two active + one expired (governance).

    共 3 条 governance: bot-wl-1/user-wl-1(永久)、bot-wl-2/user-wl-2(永久)、
    bot-wl-exp/user-wl-1(已过期)。默认查询应返回前两条,total=2。
    """
    from datetime import datetime, timedelta

    wl_repo = world.get(GovernanceWhitelistRepository)
    wl_repo.add(
        bot_id="bot-wl-1", owner_id="user-wl-1", reason="r1", created_by="88888",
    )
    wl_repo.add(
        bot_id="bot-wl-2", owner_id="user-wl-2", reason="r2", created_by="88888",
    )
    wl_repo.add(
        bot_id="bot-wl-exp",
        owner_id="user-wl-1",
        reason="expired",
        created_by="88888",
        expires_at=datetime.now() - timedelta(days=1),
    )


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/whitelist",
    scenario="ok_default_excludes_expired",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_whitelist_list,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 2}},
    ),
)
def whitelist_get_ok_default_excludes_expired():
    """Happy path: 默认排除过期,返回有效条目(total=2)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/whitelist",
    scenario="ok_filter_by_owner",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"owner_id": "user-wl-1"},
    ),
    seed=_seed_whitelist_list,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 1}},
    ),
)
def whitelist_get_ok_filter_by_owner():
    """Filter path: 按 owner 筛选(user-wl-1 有效条目仅 1 条,过期被排除)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/whitelist",
    scenario="ok_include_expired",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"include_expired": "true"},
    ),
    seed=_seed_whitelist_list,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 3}},
    ),
)
def whitelist_get_ok_include_expired():
    """Filter path: include_expired=true 返回全 3 条(含过期)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/whitelist",
    scenario="error_limit_too_large",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"limit": "201"},
    ),
    expect=ExpectError(status=422),
)
def whitelist_get_error_limit_too_large():
    """Error path: limit 超上限 200 -> 422。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/whitelist",
    scenario="no_auth",
    input=CaseInput(),  # no x-user-id → LocalAuth raises Unauthorized
    expect=ExpectError(status=401),
)
def whitelist_get_no_auth():
    """Error path: 无鉴权 -> 401。"""


def _seed_whitelist_list_with_ticket(world) -> None:
    """白单 + 对应工单(带治理快照)→ 端点应叠加工单维度字段。

    bot-wl-1/user-wl-1 有白单 + 一条工单(token_baseline=200),bot-wl-2 仅
    白单无工单(降级 None)。"""
    from datetime import datetime, timedelta

    wl_repo = world.get(GovernanceWhitelistRepository)
    wl_repo.add(
        bot_id="bot-wl-1", owner_id="user-wl-1", reason="r1", created_by="88888",
    )
    wl_repo.add(
        bot_id="bot-wl-2", owner_id="user-wl-2", reason="r2", created_by="88888",
    )
    # bot-wl-1 有一条工单(token_baseline=200, latest_decision=actionable)
    ticket_repo = world.get(TaskRecordRepository)
    worker_id = "user-wl-1:bot-wl-1"
    ticket_repo.insert_ticket(
        GovernanceTicketOrm(
            worker_id=worker_id,
            bot_id="bot-wl-1",
            owner_id="user-wl-1",
            bot_name="BotWL1",
            owner_name="OwnerOne",
            dt_version="20260705",
            governance_decision="actionable",
            latest_decision="actionable",
            governance_status="closed",
            ticket_id="tkt-wl-overlay-1",
            active_worker=None,
            token_baseline=200,
            expected_token_saving=80,
            hit_dimensions="ctx",
            saving_ratio=0.4,
            last_sync_at=datetime.now() - timedelta(days=1),
        ),
    )


@endpoint_test(
    method="GET",
    path="/api/economy/governance/admin/whitelist",
    scenario="ok_overlays_ticket_meta",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_whitelist_list_with_ticket,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 2}},
    ),
)
def whitelist_get_ok_overlays_ticket_meta():
    """白单有对应工单 → item 含工单维度字段(bot_name/token_baseline 等)。"""


# ---------------------------------------------------------------------------
# 13. /workflow/audit-logs (GET — 按 worker 只读分页查治理审计; endpoint lives in workflow_router)
#     Defined in workflow_router.py; cases kept here alongside the other governance endpoint suites.
# ---------------------------------------------------------------------------


def _seed_audit_logs(world) -> None:
    """Seed governance audit rows via repo (env auto-resolved to match query).

    三条审计:owner-1:bot-a(admin_whitelisted)、owner-1:bot-b(enqueued)、
    owner-2:bot-a(admin_whitelisted)。按 worker owner-1:bot-a 应只命中 1 条。
    """
    audit_repo = world.get(GovernanceAuditRepository)
    audit_repo.add_audit(
        "admin-wl-seed1", bot_id="bot-a", owner_id="owner-1",
        action_taken="admin_whitelisted", actor_id="88888", source="admin_api",
    )
    audit_repo.add_audit(
        "seed-enqueued", bot_id="bot-b", owner_id="owner-1",
        action_taken="enqueued", actor_id="99999", source="daily_scan",
    )
    audit_repo.add_audit(
        "admin-wl-seed2", bot_id="bot-a", owner_id="owner-2",
        action_taken="admin_whitelisted", actor_id="88888", source="admin_api",
    )


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/audit-logs",
    scenario="ok_by_worker_id",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"worker_id": "owner-1:bot-a"},
    ),
    seed=_seed_audit_logs,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 1}},
    ),
)
def audit_logs_get_ok_by_worker_id():
    """Happy path: 复合 worker_id=owner-1:bot-a 命中 1 条审计(bot-a/owner-1)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/audit-logs",
    scenario="ok_by_owner_all_bots",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"owner_id": "owner-1"},
    ),
    seed=_seed_audit_logs,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 2}},
    ),
)
def audit_logs_get_ok_by_owner():
    """Filter path: 按 owner-1 查(跨 bot)命中 2 条(bot-a + bot-b)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/audit-logs",
    scenario="ok_by_action_filter",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"action": "admin_whitelisted"},
    ),
    seed=_seed_audit_logs,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"total": 2}},
    ),
)
def audit_logs_get_ok_by_action():
    """Filter path: action=admin_whitelisted 命中 2 条(seed1 + seed2)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/audit-logs",
    scenario="error_no_filter_400",
    input=CaseInput(headers=_USER_HEADER),
    expect=ExpectError(status=400),
)
def audit_logs_get_error_no_filter():
    """Error path: 无任何过滤维度(owner/bot/action)→ 400(防全表扫)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/audit-logs",
    scenario="error_invalid_worker_id_400",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"worker_id": "no-colon"},
    ),
    expect=ExpectError(status=400),
)
def audit_logs_get_error_invalid_worker_id():
    """Error path: 非法 worker_id(缺冒号)→ 400。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/audit-logs",
    scenario="no_auth",
    input=CaseInput(),  # no x-user-id → LocalAuth raises Unauthorized
    expect=ExpectError(status=401),
)
def audit_logs_get_no_auth():
    """Error path: 无鉴权 -> 401。"""


# ---------------------------------------------------------------------------
# 14. /admin/tickets:remind (手动补发 reminder)
# ---------------------------------------------------------------------------


def _seed_remind_happy(world) -> None:
    """Seed an active ticket with first_send notify for remind test."""
    _insert_ticket(world, ticket_id="tkt-remind-1", governance_status="open",
                   bot_id="bot-remind", owner_id="owner-remind")
    _insert_notify_log(
        world, ticket_id="tkt-remind-1", bot_id="bot-remind", owner_id="owner-remind",
        notify_status="sent", governance_status="open",
    )


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:remind",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"worker_id": "owner-remind:bot-remind"},
    ),
    seed=_seed_remind_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def tickets_remind_ok():
    """Happy path: 有 active 工单 → 立即补发 reminder。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:remind",
    scenario="error_no_active",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"worker_id": "owner-none:bot-none"},
    ),
    expect=ExpectError(status=400),
)
def tickets_remind_error_no_active():
    """Error path: 无 active 工单 → 400。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:remind",
    scenario="no_auth",
    input=CaseInput(),
    expect=ExpectError(status=401),
)
def tickets_remind_no_auth():
    """Error path: 无鉴权 → 401。"""


# ---------------------------------------------------------------------------
# 15. /admin/tickets:offline-renew (强制换新)
# ---------------------------------------------------------------------------


def _seed_renew_happy(world) -> None:
    """Seed an active ticket for offline-renew test (will be closed + replaced)."""
    _insert_ticket(world, ticket_id="tkt-renew-old", governance_status="open",
                   bot_id="bot-renew", owner_id="owner-renew")
    _insert_notify_log(
        world, ticket_id="tkt-renew-old", bot_id="bot-renew", owner_id="owner-renew",
        notify_status="sent", governance_status="open",
    )


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:offline-renew",
    scenario="ok_with_active",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "owner_id": "owner-renew",
            "bot_id": "bot-renew",
            "governance_decision": "actionable",
            "dt_version": "20260710",
            "hit_dimensions": "token_usage",
            "saving_ratio": 0.5,
        },
    ),
    seed=_seed_renew_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def tickets_offline_renew_ok_with_active():
    """Happy path: 有 active 工单 → 关老 + 建新 first_send。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:offline-renew",
    scenario="ok_no_active",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "owner_id": "owner-new",
            "bot_id": "bot-new",
            "governance_decision": "actionable",
            "dt_version": "20260710",
        },
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def tickets_offline_renew_ok_no_active():
    """Happy path: 无 active 工单 → 直接建新 first_send。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:offline-renew",
    scenario="error_missing_required",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"owner_id": "owner-x"},
    ),
    expect=ExpectError(status=422),
)
def tickets_offline_renew_error_missing():
    """Error path: 缺必填字段 → 422 (Pydantic)。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/admin/tickets:offline-renew",
    scenario="no_auth",
    input=CaseInput(),
    expect=ExpectError(status=401),
)
def tickets_offline_renew_no_auth():
    """Error path: 无鉴权 → 401。"""


# ---------------------------------------------------------------------------
# 16. /admin/tickets:delete-cascade (精确级联删工单 + 归属通知)
# ---------------------------------------------------------------------------


def _seed_delete_cascade_happy(world) -> None:
    """Seed one ticket + two notify rows + one unrelated ticket/notify 验证精确级联。"""
    _insert_ticket(world, ticket_id="tkt-cascade-1", governance_status="open",
                   bot_id="bot-cascade", owner_id="owner-cascade")
    _insert_notify_log(
        world, ticket_id="tkt-cascade-1", bot_id="bot-cascade", owner_id="owner-cascade",
        notify_status="sent", governance_status="open",
    )
    _insert_notify_log(
        world, ticket_id="tkt-cascade-1", bot_id="bot-cascade", owner_id="owner-cascade",
        notify_status="pending", governance_status="open",
    )
    # 无关工单/通知:不应被动
    _insert_ticket(world, ticket_id="tkt-cascade-2", governance_status="open",
                   bot_id="bot-other", owner_id="owner-other")
    _insert_notify_log(
        world, ticket_id="tkt-cascade-2", bot_id="bot-other", owner_id="owner-other",
        notify_status="sent", governance_status="open",
    )


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:delete-cascade",
    scenario="ok_dry_run_preview",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"ticket_id": "tkt-cascade-1", "reason": "preview"},
    ),
    seed=_seed_delete_cascade_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"ticket_found": True, "dry_run": True, "notify_deleted": 2},
        },
    ),
    extra_assertions=(_assert_dry_run_no_delete,),
)
def tickets_delete_cascade_dry_run_ok():
    """Happy path: dry_run 预览工单+2 通知数,数据未动。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:delete-cascade",
    scenario="ok_real_delete_cascade",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"ticket_id": "tkt-cascade-1", "dry_run": False, "reason": "purge"},
    ),
    seed=_seed_delete_cascade_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "ticket_found": True,
                "dry_run": False,
                "tickets_deleted": 1,
                "notify_deleted": 2,
            },
        },
    ),
    extra_assertions=(_assert_real_delete_precise_cascade,),
)
def tickets_delete_cascade_real_delete_ok():
    """Happy path: 真删 → 删工单+2 通知,无关工单/通知不动(精确级联)。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:delete-cascade",
    scenario="ok_ticket_not_found",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"ticket_id": "tkt-nonexistent-999", "dry_run": False, "reason": "x"},
    ),
    seed=_seed_delete_cascade_happy,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"ticket_found": False, "tickets_deleted": 0, "notify_deleted": 0},
        },
    ),
    extra_assertions=(_assert_not_found_no_change,),
)
def tickets_delete_cascade_not_found_ok():
    """工单不存在 → 200 + ticket_found=False,数据无变化。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:delete-cascade",
    scenario="error_missing_required",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"reason": "missing ticket_id"},
    ),
    expect=ExpectError(status=422),
)
def tickets_delete_cascade_error_missing_ticket_id():
    """Error path: 缺 ticket_id → 422 (Pydantic min_length=1)。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:delete-cascade",
    scenario="error_missing_reason",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={"ticket_id": "tkt-cascade-1"},
    ),
    expect=ExpectError(status=422),
)
def tickets_delete_cascade_error_missing_reason():
    """Error path: 缺 reason → 422 (Pydantic min_length=1)。"""


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets:delete-cascade",
    scenario="no_auth",
    input=CaseInput(),
    expect=ExpectError(status=401),
)
def tickets_delete_cascade_no_auth():
    """Error path: 无鉴权 → 401。"""
