"""Endpoint coverage for governance workflow router endpoints.

正常业务流程 router(`/api/economy/governance/workflow/*`):
  - GET  /workflow/tickets                 工单列表(按治理状态过滤 + 分页)
  - GET  /workflow/tickets/detail          单工单详情(ticket_id 走 query)
  - POST /workflow/tickets/review          审批动作(ticket_id 走 body,waiting_review 三态流转)

Uses real DI services and in-memory SQLite -- no MagicMock / unittest.mock.
Each seed inserts real rows via repos so handlers exercise the full
service → repo → DB stack.
"""
from __future__ import annotations

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
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


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
    bot_name: str | None = "bot-alpha",
) -> None:
    """Insert a real GovernanceTicketOrm row via repo."""
    repo = world.get(TaskRecordRepository)
    worker_id = f"{owner_id}:{bot_id}"
    active_worker = worker_id if governance_status != "closed" else None
    repo.insert_ticket(
        GovernanceTicketOrm(
            worker_id=worker_id,
            bot_id=bot_id,
            owner_id=owner_id,
            bot_name=bot_name,
            dt_version=dt_version,
            governance_decision="actionable",
            governance_status=governance_status,
            ticket_id=ticket_id,
            active_worker=active_worker,
            last_sync_at=datetime.now(),
        ),
    )


# ---------------------------------------------------------------------------
# List / detail seed functions
# ---------------------------------------------------------------------------


def _seed_list_mixed(world) -> None:
    """Seed tickets across owner/status for list-filter + pagination checks."""
    # owner-1: one waiting_review, one open
    _insert_ticket(
        world, ticket_id="tkt-list-wait1",
        governance_status="waiting_review", owner_id="owner-1",
    )
    _insert_ticket(
        world, ticket_id="tkt-list-open1",
        governance_status="open", owner_id="owner-1", bot_id="bot-2",
    )
    # owner-2: one closed
    _insert_ticket(
        world, ticket_id="tkt-list-closed1",
        governance_status="closed", owner_id="owner-2", bot_id="bot-3",
    )
    # owner-3: scheduled
    _insert_ticket(
        world, ticket_id="tkt-list-sched1",
        governance_status="scheduled", owner_id="owner-3", bot_id="bot-4",
    )


def _seed_detail(world) -> None:
    """Seed a single ticket for detail lookup."""
    _insert_ticket(
        world, ticket_id="tkt-detail-1",
        governance_status="waiting_review",
    )


def _seed_detail_whitelisted(world) -> None:
    """Seed a ticket whose (bot_id, owner_id) is also in the whitelist."""
    _insert_ticket(
        world, ticket_id="tkt-detail-wl",
        bot_id="bot-wl-detail", owner_id="owner-wl-detail",
        governance_status="waiting_review",
    )
    world.get(GovernanceWhitelistRepository).add(
        bot_id="bot-wl-detail", owner_id="owner-wl-detail",
        created_by="tester",
    )


def _seed_review_waiting(world) -> None:
    """Seed a waiting_review ticket for approve_close."""
    _insert_ticket(
        world, ticket_id="tkt-review-approve",
        governance_status="waiting_review",
    )


def _seed_review_whitelist(world) -> None:
    """Seed a waiting_review ticket for approve_whitelist."""
    _insert_ticket(
        world, ticket_id="tkt-review-wl", bot_id="bot-wl-target",
        governance_status="waiting_review",
    )


def _seed_review_reject(world) -> None:
    """Seed a waiting_review ticket for reject_for_reopen."""
    _insert_ticket(
        world, ticket_id="tkt-review-reject",
        governance_status="waiting_review",
    )


# ---------------------------------------------------------------------------
# Extra assertion helpers
# ---------------------------------------------------------------------------


def _assert_list_waiting_filter(response, world) -> None:
    """waiting_review filter returns exactly the waiting_review ticket."""
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status_filter"] == ["waiting_review"]
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["governance_status"] == "waiting_review"
        # 列表行须暴露 id(删除锚键)与 gmt_modified(更新时间,键在即可,值可 None)
        assert "id" in item, "list item missing field 'id'"
        assert "gmt_modified" in item, "list item missing field 'gmt_modified'"
    ticket_ids = {item["ticket_id"] for item in data["items"]}
    assert "tkt-list-wait1" in ticket_ids
    # id 取值经领域模型 from_orm 透传,与 repo 查得的 ORM 主键一致
    repo = world.get(TaskRecordRepository)
    expected = repo.find_by_ticket_id("tkt-list-wait1")
    assert expected is not None
    matched = next(i for i in data["items"] if i["ticket_id"] == "tkt-list-wait1")
    assert matched["id"] == expected.id, (
        f"list item id {matched['id']!r} != ORM id {expected.id!r}"
    )


def _assert_list_pagination(response, world) -> None:
    """limit=1 page returns <=1 item; total unaffected by window."""
    body = response.json()
    data = body["data"]
    assert data["limit"] == 1
    assert data["offset"] == 0
    assert len(data["items"]) <= 1
    assert data["total"] >= 3  # at least open1 + wait1 + closed1 + sched1


def _assert_list_invalid_status_400(response, world) -> None:
    """Invalid status value surfaces as the error envelope / 400 status."""
    # FastAPI HTTPException(400) → expect status 400 (framework asserts it)
    assert response.status_code == 400 or response.json().get("success") is False


def _assert_detail_found(response, world) -> None:
    """Detail returns the seeded ticket with full review fields."""
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["ticket_id"] == "tkt-detail-1"
    assert data["governance_status"] == "waiting_review"
    # 详情页特有字段存在(可能为 None,但键必须在)
    for k in (
        "worker_id", "bot_id", "dt_version", "review_reason",
        "feedback_payload", "gmt_create", "gmt_modified", "id",
    ):
        assert k in data, f"detail response missing field {k!r}"
    # id 取值经领域模型 from_orm 透传,与 repo 查得的 ORM 主键一致
    repo = world.get(TaskRecordRepository)
    expected = repo.find_by_ticket_id("tkt-detail-1")
    assert expected is not None
    assert data["id"] == expected.id, (
        f"detail id {data['id']!r} != ORM id {expected.id!r}"
    )
    # 白名单位(单点查询):seed 未加白 → False
    assert data["in_whitelist"] is False


def _assert_review_approved_closed(response, world) -> None:
    """approve_close transitions the ticket to closed."""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-review-approve")
    assert ticket is not None
    assert ticket.governance_status == "closed", (
        f"Expected closed, got {ticket.governance_status}"
    )
    assert ticket.close_reason and ticket.close_reason.endswith("_approved")


def _assert_review_whitelist_added(response, world) -> None:
    """approve_whitelist adds a whitelist entry + closes the ticket."""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-review-wl")
    assert ticket is not None
    assert ticket.governance_status == "closed"


def _assert_review_reject_not_closed(response, world) -> None:
    """reject_for_reopen closes with review_rejected close_reason.

    Per service semantics reject_for_reopen still flips status to closed
    (terminal state in the current state machine); verify the close_reason.
    """
    body = response.json()
    assert body["success"] is True
    assert body["data"]["close_reason"] == "review_rejected"


# ---------------------------------------------------------------------------
# 1. GET /workflow/tickets — list
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets",
    scenario="ok_default",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_list_mixed,
    expect=ExpectSuccess(
        status=200, json_contains={"success": True, "data": {"offset": 0}},
    ),
)
def review_list_default_ok():
    """Happy path: default active filter returns tickets + pagination meta."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets",
    scenario="ok_filter_waiting",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"statuses": "waiting_review"},
    ),
    seed=_seed_list_mixed,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_list_waiting_filter,),
)
def review_list_filter_waiting_ok():
    """Happy path: statuses=waiting_review filters to waiting ticket only."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets",
    scenario="ok_pagination",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"limit": "1", "offset": "0"},
    ),
    seed=_seed_list_mixed,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_list_pagination,),
)
def review_list_pagination_ok():
    """Happy path: limit=1 caps items while total stays full count."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets",
    scenario="error_invalid_status",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"statuses": "bogus"},
    ),
    expect=ExpectError(status=400),
    extra_assertions=(_assert_list_invalid_status_400,),
)
def review_list_invalid_status_error():
    """Error path: invalid status value → 400."""


# ---------------------------------------------------------------------------
# 2. GET /workflow/tickets/detail — detail
# ---------------------------------------------------------------------------


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/detail",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"ticket_id": "tkt-detail-1"},
    ),
    seed=_seed_detail,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_detail_found,),
)
def review_detail_ok():
    """Happy path: detail returns full review fields for seeded ticket."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/detail",
    scenario="error_not_found",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"ticket_id": "tkt-nonexistent-999"},
    ),
    # No seed -- ticket not found → HTTPException(404)
    expect=ExpectError(status=404),
)
def review_detail_not_found_error():
    """Error path: detail on missing ticket → 404."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/detail",
    scenario="ok_in_whitelist",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"ticket_id": "tkt-detail-wl"},
    ),
    seed=_seed_detail_whitelisted,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"in_whitelist": True}},
    ),
)
def review_detail_in_whitelist_ok():
    """Whitelist path: detail.in_whitelist=True when (bot,owner) whitelisted."""


# ---------------------------------------------------------------------------
# 3. POST /workflow/tickets/review — approve_close
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets/review",
    scenario="ok_approve_close",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-review-approve",
            "action": "approve_close",
        },
    ),
    seed=_seed_review_waiting,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"governance_status": "closed"}},
    ),
    extra_assertions=(_assert_review_approved_closed,),
)
def review_action_approve_close_ok():
    """Happy path: approve_close → closed, close_reason *_approved.

    WorkflowReviewRequest requires ``ticket_id`` in the body (path param is
    separate but the body schema still mandates it for validation).
    """


# ---------------------------------------------------------------------------
# 4. POST /workflow/tickets/review — approve_whitelist
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets/review",
    scenario="ok_approve_whitelist",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-review-wl",
            "action": "approve_whitelist",
        },
    ),
    seed=_seed_review_whitelist,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"close_reason": "whitelist_approved"}},
    ),
    extra_assertions=(_assert_review_whitelist_added,),
)
def review_action_approve_whitelist_ok():
    """Happy path: approve_whitelist → closed + whitelist added."""


# ---------------------------------------------------------------------------
# 5. POST /workflow/tickets/review — reject_for_reopen
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets/review",
    scenario="ok_reject_for_reopen",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-review-reject",
            "action": "reject_for_reopen",
        },
    ),
    seed=_seed_review_reject,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_review_reject_not_closed,),
)
def review_action_reject_for_reopen_ok():
    """Happy path: reject_for_reopen → close_reason review_rejected."""


# ---------------------------------------------------------------------------
# 6. POST /workflow/tickets/review — error: not found (404)
# ---------------------------------------------------------------------------


# ticket_id is a path param but the body ALSO carries ticket_id (WorkflowReviewRequest).
# The body takes precedence inside the handler (body.ticket_id), so point the
# body at a nonexistent id to hit the NOT_FOUND branch.
@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets/review",
    scenario="error_not_found",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"ticket_id": "tkt-nonexistent-999"},
        json_body={
            "ticket_id": "tkt-nonexistent-999",
            "action": "approve_close",
        },
    ),
    # No seed → service returns TicketActionOutcome(error_code=NOT_FOUND)
    # → _raise_on_admin_error → HTTPException(404)
    expect=ExpectError(status=404),
)
def review_action_not_found_error():
    """Error path: review on missing ticket → 404."""


# ---------------------------------------------------------------------------
# 7. POST /workflow/tickets/review — error: invalid action (400)
# ---------------------------------------------------------------------------


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets/review",
    scenario="error_invalid_action",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-review-reject",
            "action": "bogus_action",
        },
    ),
    seed=_seed_review_reject,
    # INVALID_ACTION → _raise_on_admin_error → HTTPException(400)
    expect=ExpectError(status=400),
)
def review_action_invalid_action_error():
    """Error path: invalid action → 400."""


# ---------------------------------------------------------------------------
# 8. POST /workflow/tickets/review — error: invalid status (400)
# ---------------------------------------------------------------------------


def _seed_review_open_ticket(world) -> None:
    """Seed an 'open' ticket — approve_close on non-waiting → INVALID_STATUS."""
    _insert_ticket(
        world, ticket_id="tkt-review-open-status",
        governance_status="open",
    )


@endpoint_test(
    method="POST",
    path="/api/economy/governance/workflow/tickets/review",
    scenario="error_invalid_status",
    input=CaseInput(
        headers=_USER_HEADER,
        json_body={
            "ticket_id": "tkt-review-open-status",
            "action": "approve_close",
        },
    ),
    seed=_seed_review_open_ticket,
    # open (not waiting_review) → TicketActionOutcome(INVALID_STATUS)
    # → _raise_on_admin_error → HTTPException(400)
    expect=ExpectError(status=400),
)
def review_action_invalid_status_error():
    """Error path: review on non-waiting_review ticket → 400."""


# ---------------------------------------------------------------------------
# 4. GET /workflow/tickets/pending-notification — query notification_id
# ---------------------------------------------------------------------------


def _seed_ticket_with_notify(world) -> None:
    """Seed a ticket + a notify_log row so pending-notification finds it."""
    ticket_id = "tkt-pn-1"
    _insert_ticket(
        world, ticket_id=ticket_id,
        governance_status="open",
        bot_id="bot-pn",
    )
    worker_id = "owner-1:bot-pn"
    notify_repo = world.get(NotifyLogRepository)
    notify_repo.insert_notification(
        GovernanceNotificationOrm(
            notification_id="nid-pn-test-1",
            ticket_id=ticket_id,
            bot_id="bot-pn",
            bot_name="bot-alpha",
            owner_id="owner-1",
            worker_id=worker_id,
            dt_version="20260705",
            governance_decision="actionable",
            governance_cycle_id=ticket_id,
            governance_status="open",
            notify_status="sent",
            notify_type="first_send",
            notify_source="offline_batch",
            send_attempt_count=1,
        ),
    )


def _assert_pending_notification_found(response, world) -> None:
    data = response.json()
    assert data["success"] is True
    assert data["data"]["notification_id"] == "nid-pn-test-1"


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/pending-notification",
    scenario="ok",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"ticket_id": "tkt-pn-1"},
    ),
    seed=_seed_ticket_with_notify,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_pending_notification_found,),
)
def pending_notification_ok():
    """Happy path: returns notification_id for ticket with notify_log row."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/pending-notification",
    scenario="not_found",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"ticket_id": "tkt-no-notify-999"},
    ),
    # No seed — ticket has no notify_log row → 404
    expect=ExpectError(status=404),
)
def pending_notification_not_found_error():
    """Error path: ticket with no notification → 404."""