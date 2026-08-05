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
    # owner-4: observed(白名单观察态,active_worker=None)
    _insert_ticket(
        world, ticket_id="tkt-list-obs1",
        governance_status="observed", owner_id="owner-4", bot_id="bot-5",
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


def _assert_list_observed_filter(response, world) -> None:
    """observed filter 返回观察态工单(评审据此查看白名单 bot 最新画像,Task 13)。"""
    del world
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status_filter"] == ["observed"]
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["governance_status"] == "observed"
    ticket_ids = {item["ticket_id"] for item in data["items"]}
    assert "tkt-list-obs1" in ticket_ids


def _assert_list_pagination(response, world) -> None:
    """limit=1 page returns <=1 item; total unaffected by window."""
    body = response.json()
    data = body["data"]
    assert data["limit"] == 1
    assert data["offset"] == 0
    assert len(data["items"]) <= 1
    assert data["total"] >= 3  # at least open1 + wait1 + closed1 + sched1


def _assert_whitelist_endpoint(response, world) -> None:
    """tickets:whitelist 端点返回 OBSERVED 工单(纯工单 item,含治理画像字段)。"""
    del world
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    # status_filter 标识白单视图 = observed
    assert data["status_filter"] == ["observed"]
    assert data["total"] >= 1
    for item in data["items"]:
        # 全是 OBSERVED 观察态(加白中 bot)
        assert item["governance_status"] == "observed"
        # item 是纯工单(ReviewTicketItem),含治理画像字段(供 admin 评估是否继续留白;
        # 不并白单元数据。dt_version 在详情 schema 暴露,列表 item 无此字段,不在此断言)
        assert "token_baseline" in item
        assert "hit_dimensions" in item
        assert "saving_ratio" in item
        assert "latest_decision" in item
    ticket_ids = {item["ticket_id"] for item in data["items"]}
    assert "tkt-list-obs1" in ticket_ids


def _assert_whitelist_endpoint_empty(response, world) -> None:
    """无 OBSERVED 工单时 tickets:whitelist 返回空列表 + total=0,不报错。"""
    del world
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["status_filter"] == ["observed"]
    assert data["total"] == 0
    assert data["items"] == []


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
    """approve_whitelist adds a whitelist entry + observes the ticket (OBSERVED)."""
    repo = world.get(TaskRecordRepository)
    ticket = repo.find_by_ticket_id("tkt-review-wl")
    assert ticket is not None
    assert ticket.governance_status == "observed"


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
    scenario="ok_filter_observed",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"statuses": "observed"},
    ),
    seed=_seed_list_mixed,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_list_observed_filter,),
)
def review_list_filter_observed_ok():
    """Happy path: statuses=observed 筛出白名单观察态工单(Task 13 可见性)。"""


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


def _seed_delivery_variants(world) -> None:
    """Seed open tickets with different delivery_status 列值(含 none 历史哨兵)。"""
    repo = world.get(TaskRecordRepository)
    # 列默认 none(未建通知 — 新工单/OBSERVED 不建 notify 保持 none)
    repo.insert_ticket(GovernanceTicketOrm(
        ticket_id="tkt-deliv-none", worker_id="owner-d:bot-none",
        bot_id="bot-none", owner_id="owner-d", bot_name="BotNone",
        dt_version="20260705", governance_decision="actionable",
        governance_status="open", active_worker="owner-d:bot-none",
        delivery_status="none", last_sync_at=datetime.now(),
    ))
    # pending(已建 first_send 待发)
    repo.insert_ticket(GovernanceTicketOrm(
        ticket_id="tkt-deliv-pending", worker_id="owner-d:bot-pend",
        bot_id="bot-pend", owner_id="owner-d", bot_name="BotPending",
        dt_version="20260705", governance_decision="actionable",
        governance_status="open", active_worker="owner-d:bot-pend",
        delivery_status="pending", last_sync_at=datetime.now(),
    ))


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets",
    scenario="ok_delivery_none_not_422",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"statuses": "open", "delivery_status": "none"},
    ),
    seed=_seed_delivery_variants,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def review_list_delivery_none_ok():
    """delivery_status=none(历史哨兵)不再 422,返回列值 none 的工单。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets",
    scenario="ok_delivery_multi_value",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={
            "statuses": "open",
            "delivery_status": ["pending", "none"],
        },
    ),
    seed=_seed_delivery_variants,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def review_list_delivery_multi_value_ok():
    """多值 delivery_status(pending+none)IN 匹配两值,返回两类工单。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets:whitelist",
    scenario="ok_whitelist_view",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_list_mixed,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_whitelist_endpoint,),
)
def whitelist_tickets_ok():
    """Happy path: tickets:whitelist 返回 OBSERVED 观察态工单(白单 bot 最新治理画像)。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets:whitelist",
    scenario="ok_whitelist_empty",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_detail,  # 仅一条 waiting_review,无 OBSERVED
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_whitelist_endpoint_empty,),
)
def whitelist_tickets_empty():
    """Empty path: 无 OBSERVED 工单 → items=[] total=0,不报错。"""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets:whitelist",
    scenario="error_invalid_limit",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"limit": "0"},  # limit ge=1,0 违规 → FastAPI 422
    ),
    expect=ExpectError(status=422),
)
def whitelist_tickets_invalid_limit_error():
    """Error path: limit < 1 → 422(FastAPI Query 校验)。"""


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


# ---------------------------------------------------------------------------
# 9. GET /workflow/tickets/by-worker — 按 worker 查工单历史(辅助决策)
# ---------------------------------------------------------------------------


def _seed_worker_history(world) -> None:
    """Seed one worker with 2 historical-closed + 1 active-open ticket.

    历史 closed 行用 last_sync_at 拉差以稳定 gmt_create 顺序(SQLite 秒级精度,
    取毫秒差避免同秒插入导致 gmt_create 并列)。active 工单至多一条(UK active_worker)。
    """
    from datetime import datetime, timedelta
    repo = world.get(TaskRecordRepository)
    base = datetime.now()
    worker_id = "owner-h:bot-h"
    # 老:closed(active_worker=None)。显式 gmt_create 拉开天数,保证 repo 的
    # gmt_create DESC 确定排序(SQLite TIMESTAMP 秒级精度,不显式设会同时刻插入)。
    repo.insert_ticket(GovernanceTicketOrm(
        ticket_id="tkt-hw-old", worker_id=worker_id,
        bot_id="bot-h", owner_id="owner-h", bot_name="BotH",
        dt_version="20260701", governance_decision="actionable",
        governance_status="closed", active_worker=None,
        close_reason="admin_closed", close_conclusion="false_positive",
        closed_at=base - timedelta(days=2), delivery_status="sent",
        gmt_create=base - timedelta(days=2), last_sync_at=base - timedelta(days=2),
    ))
    # 中:closed
    repo.insert_ticket(GovernanceTicketOrm(
        ticket_id="tkt-hw-mid", worker_id=worker_id,
        bot_id="bot-h", owner_id="owner-h", bot_name="BotH",
        dt_version="20260705", governance_decision="actionable",
        governance_status="closed", active_worker=None,
        close_reason="review_rejected", closed_at=base - timedelta(days=1),
        delivery_status="sent", gmt_create=base - timedelta(days=1),
        last_sync_at=base - timedelta(days=1),
    ))
    # 新:open(active)
    repo.insert_ticket(GovernanceTicketOrm(
        ticket_id="tkt-hw-new", worker_id=worker_id,
        bot_id="bot-h", owner_id="owner-h", bot_name="BotH",
        dt_version="20260710", governance_decision="actionable",
        governance_status="open", active_worker=worker_id,
        delivery_status="pending", gmt_create=base, last_sync_at=base,
    ))
    # 别的 worker,不应混入
    repo.insert_ticket(GovernanceTicketOrm(
        ticket_id="tkt-hw-other", worker_id="owner-x:bot-x",
        bot_id="bot-x", owner_id="owner-x", bot_name="BotX",
        dt_version="20260710", governance_decision="actionable",
        governance_status="open", active_worker="owner-x:bot-x",
        last_sync_at=base,
    ))


def _assert_worker_history_ok(response, world) -> None:
    del world
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["worker_id"] == "owner-h:bot-h"
    assert data["owner_id"] == "owner-h"
    assert data["bot_id"] == "bot-h"
    assert data["limit"] == 5
    ids = [it["ticket_id"] for it in data["items"]]
    assert ids == ["tkt-hw-new", "tkt-hw-mid", "tkt-hw-old"]
    # 决策字段齐全
    new_item = data["items"][0]
    assert new_item["governance_status"] == "open"
    assert new_item["delivery_status"] == "pending"
    old_item = data["items"][2]
    assert old_item["close_reason"] == "admin_closed"
    assert old_item["close_conclusion"] == "false_positive"


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="ok_worker_id",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"worker_id": "owner-h:bot-h"},
    ),
    seed=_seed_worker_history,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_worker_history_ok,),
)
def tickets_by_worker_ok():
    """Happy path: worker_id 返回 3 条历史 + 活跃,gmt_create 倒序,回显 worker。"""


def _seed_worker_history_many(world) -> None:
    """Seed 3 closed-history tickets for a worker (limit cap test)."""
    from datetime import datetime, timedelta
    repo = world.get(TaskRecordRepository)
    base = datetime.now()
    worker_id = "owner-l:bot-l"
    for i in range(3):
        repo.insert_ticket(GovernanceTicketOrm(
            ticket_id=f"tkt-lim-{i}", worker_id=worker_id,
            bot_id="bot-l", owner_id="owner-l", bot_name="BotL",
            dt_version=f"2026070{i}", governance_decision="actionable",
            governance_status="closed", active_worker=None,
            close_reason="stale_replaced", close_conclusion=None,
            closed_at=base - timedelta(days=2 - i),
            delivery_status="sent", last_sync_at=base - timedelta(days=2 - i),
        ))


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="ok_limit_cap",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"worker_id": "owner-l:bot-l", "limit": "1"},
    ),
    seed=_seed_worker_history_many,
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"limit": 1}}),
)
def tickets_by_worker_limit():
    """Happy path: limit=1 caps to most recent ticket."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="ok_owner_only",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"owner_id": "owner-h"},
    ),
    seed=_seed_worker_history,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def tickets_by_worker_owner_only():
    """Happy path: owner-only query returns matches; worker_id echo None."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="empty_no_match",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"worker_id": "nobody:none"},
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True, "data": {"items": []}}),
)
def tickets_by_worker_empty():
    """Happy path: no tickets for worker → 200 items=[]."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="error_missing_all",
    input=CaseInput(headers=_USER_HEADER),
    # 全空定位 → service ValueError → 400
    expect=ExpectError(status=400),
)
def tickets_by_worker_missing_all_error():
    """Error path: no worker/owner/bot → 400."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="error_invalid_worker_id",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"worker_id": "no-colon"},
    ),
    # worker_id 非法 → service ValueError → 400
    expect=ExpectError(status=400),
)
def tickets_by_worker_invalid_worker_id_error():
    """Error path: worker_id without colon → 400."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="error_limit_out_of_range_low",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"owner_id": "owner-h", "limit": "0"},
    ),
    # limit < 1 → FastAPI Query ge 校验 → 422
    expect=ExpectError(status=422),
)
def tickets_by_worker_limit_zero_error():
    """Error path: limit=0 → 422."""


@endpoint_test(
    method="GET",
    path="/api/economy/governance/workflow/tickets/by-worker",
    scenario="error_limit_out_of_range_high",
    input=CaseInput(
        headers=_USER_HEADER,
        query_params={"owner_id": "owner-h", "limit": "51"},
    ),
    # limit > 50 → FastAPI Query le 校验 → 422
    expect=ExpectError(status=422),
)
def tickets_by_worker_limit_over_fifty_error():
    """Error path: limit=51 → 422."""