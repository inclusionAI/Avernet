"""Endpoint tests for task-discovery public routes.

Covers:
- POST /api/v1/collaboration/tasks/discovery/discover (happy + error)
- GET  /api/v1/collaboration/tasks/discovery/status  (happy + coverage)

Happy cases assert only ``{"code": 200000}`` — the handler returns the
unified success ``Envelope`` when the discovery flow runs end-to-end,
regardless of whether any tasks are found in the DB (session-creation
errors are captured per-task, not at the top level).  This makes the
cases robust in both CI (empty DB → empty discoveries) and local dev
(seeded DB with test data).

The POST error case is a FastAPI 422 (missing required bot_id).
The status coverage case seeds tasks via ORM and verifies the
aggregation of persisted tasks with process-local discovery results.
"""
from __future__ import annotations

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryResult,
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    seed_discovered_tasks,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

# ---- POST /api/v1/collaboration/tasks/discovery/discover ----

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/discover",
    scenario="happy_ok",
    input=CaseInput(query_params={"bot_id": "td-bot-1", "owner_id": "td-owner-1"}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000},
    ),
)
def discover_tasks_happy_ok():
    """Happy path: discovery runs end-to-end, returns success envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/discover",
    scenario="err_missing_bot_id",
    input=CaseInput(query_params={"owner_id": "td-owner-1"}),
    expect=ExpectError(status=422),
)
def discover_tasks_err_missing_bot_id():
    """Error path: missing required bot_id -> FastAPI 422."""


# ---- GET /api/v1/collaboration/tasks/discovery/status ----

@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/discovery/status",
    scenario="happy_ok",
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000},
    ),
)
def get_status_happy_ok():
    """Happy path: status endpoint returns success envelope."""


# Exercise the status aggregation loop with both a task that has an in-memory
# discovery result and one that has not been discovered in this process.
_STATUS_DISCOVERED_TASK_ID = "status-discovered"
_STATUS_PENDING_TASK_ID = "status-pending"


def _status_task(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "bot_id": "status-bot",
        "owner_id": "status-owner",
        "dt": "2026-08-20",
        "title": task_id,
        "instruction": "status coverage",
        "background": "testing",
        "discovery_basis": "changed-line coverage",
        "priority": "medium",
        "status": "pending_confirmation",
        "objective": f"目标:{task_id}",
        "acceptances": [{"id": "c1", "description": "验收-1"}],
    }


def _seed_status_with_discovered_and_pending_tasks(world) -> None:
    db = world.get(DatabasePlugin)
    seed_discovered_tasks(db, [
        _status_task(_STATUS_DISCOVERED_TASK_ID),
        _status_task(_STATUS_PENDING_TASK_ID),
    ])
    service = world.get(DiscoveryService)
    _task_dto = _status_task(_STATUS_DISCOVERED_TASK_ID)
    task = DiscoveredTask(
        task_id=_task_dto["task_id"],
        bot_id=_task_dto["bot_id"],
        owner_id=_task_dto["owner_id"],
        dt=_task_dto["dt"],
        title=_task_dto["title"],
        instruction=_task_dto["instruction"],
        background=_task_dto["background"],
        discovery_basis=_task_dto["discovery_basis"],
        priority=_task_dto["priority"],
        status=_task_dto["status"],
        objective=_task_dto.get("objective", ""),
        acceptances=list(_task_dto.get("acceptances", [])),
    )
    service._discoveries[_STATUS_DISCOVERED_TASK_ID] = DiscoveryResult(
        task=task,
        session=DiscoverySession(
            task_id=task.task_id,
            session_id="status-session",
            session_url="http://localhost/session/status-session",
        ),
        notification_sent=True,
    )


def _cleanup_status_coverage(response, world) -> None:
    db = world.get(DatabasePlugin)
    with db.transactional_orm_session() as session:
        from agentclaw.community.core.task.task_discovery.discovered_task_models import (
            DiscoveredTaskModel,
        )
        session.query(DiscoveredTaskModel).delete()


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/discovery/status",
    scenario="happy_discovered_and_pending",
    seed=_seed_status_with_discovered_and_pending_tasks,
    extra_assertions=(_cleanup_status_coverage,),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 2,
                "discovered": 1,
                "tasks": [
                    {
                        "task_id": _STATUS_DISCOVERED_TASK_ID,
                        "discovered": True,
                        "session_id": "status-session",
                        "notification_sent": True,
                    },
                    {
                        "task_id": _STATUS_PENDING_TASK_ID,
                        "discovered": False,
                        "session_id": None,
                        "notification_sent": False,
                        "error": None,
                    },
                ],
            },
        },
    ),
)
def get_status_happy_discovered_and_pending():
    """Status joins persisted tasks with process-local discovery results."""


# ---- POST /api/v1/collaboration/tasks/discovery/reschedule ----

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/reschedule",
    scenario="happy",
    input=CaseInput(query_params={"cron": "30 14 * * *"}),
    expect=ExpectSuccess(status=200),
)
def reschedule_happy():
    """Happy path: valid cron expression accepted (scheduler may or may not be running)."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/reschedule",
    scenario="error",
    expect=ExpectError(status=422),
)
def reschedule_err_missing_cron():
    """Error path: missing required cron query param -> FastAPI 422."""


# ---- POST /api/v1/collaboration/tasks/discovery/dingtalk-config ----

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/dingtalk-config",
    scenario="happy",
    input=CaseInput(json_body={
        "ak_id": "test-ak-id",
        "ak_secret": "test-ak-secret",
        "robot_code": "test-robot",
        "card_template_id": "test-template.schema",
    }),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def dingtalk_config_happy():
    """Happy path: valid credentials injected successfully."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/dingtalk-config",
    scenario="error",
    expect=ExpectError(status=422),
)
def dingtalk_config_err_missing_body():
    """Error path: missing required body -> FastAPI 422."""


# ---- GET /api/v1/collaboration/tasks/discovery/status ---- (error case)

def _seed_status_reader_error(world) -> None:
    """Override DatabasePlugin.orm_session to raise — OrmTaskReader propagates
    a RuntimeError → handler catches → InternalError → 500 ErrorEnvelope."""
    def _fail(*_args, **_kwargs):
        raise RuntimeError("status read failed (gate)")

    world.get(DatabasePlugin).set_override("orm_session", _fail)


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/discovery/status",
    scenario="error",
    seed=_seed_status_reader_error,
    expect=ExpectError(status=500),
)
def get_status_err_reader_failure():
    """Error path: DatabasePlugin.orm_session raises → handler InternalError → 500."""


# ---- POST /api/v1/collaboration/tasks/discovery/tasks ----

def _seed_write_tasks_ok(world) -> None:
    """Pre-clean ac_discovered_tasks so the upsert happy path runs against an empty table."""
    db = world.get(DatabasePlugin)
    from agentclaw.community.core.task.task_discovery.discovered_task_models import (
        DiscoveredTaskModel,
    )
    with db.transactional_orm_session() as session:
        session.query(DiscoveredTaskModel).delete()


def _seed_write_tasks_error(world) -> None:
    """Override DatabasePlugin.transactional_orm_session to raise → handler InternalError → 500."""
    def _fail(*_args, **_kwargs):
        raise RuntimeError("upsert failed (gate)")

    world.get(DatabasePlugin).set_override("transactional_orm_session", _fail)


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/tasks",
    scenario="happy",
    input=CaseInput(json_body={
        "tasks": [
            {
                "task_id": "td-write-1",
                "bot_id": "td-bot-1",
                "owner_id": "td-owner-1",
                "dt": "2026-08-26",
                "title": "Write-happy task",
                "instruction": "instruction",
                "background": "background",
                "discovery_basis": "basis",
                "priority": "medium",
                "status": "pending_confirmation",
                "objective": "",
                "acceptances": [],
            }
        ]
    }),
    seed=_seed_write_tasks_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000},
    ),
)
def write_tasks_happy():
    """Happy path: upsert one task → returns Envelope with code=200000, written=1."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/discovery/tasks",
    scenario="error",
    seed=_seed_write_tasks_error,
    input=CaseInput(json_body={"tasks": [
        {"task_id": "td-err-1", "bot_id": "b", "owner_id": "o", "dt": "2026-08-26",
         "title": "x", "instruction": "y", "background": "z"}
    ]}),
    expect=ExpectError(status=500),
)
def write_tasks_err_db_unavailable():
    """Error path: DatabasePlugin.transactional_orm_session raises → InternalError → 500."""


# ---- DELETE /api/v1/collaboration/tasks/discovery/tasks ----

def _seed_clear_tasks_error(world) -> None:
    """Override DatabasePlugin.transactional_orm_session to raise → handler InternalError → 500."""
    def _fail(*_args, **_kwargs):
        raise RuntimeError("clear failed (gate)")

    world.get(DatabasePlugin).set_override("transactional_orm_session", _fail)


@endpoint_test(
    method="DELETE",
    path="/api/v1/collaboration/tasks/discovery/tasks",
    scenario="happy",
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000},
    ),
)
def clear_tasks_happy():
    """Happy path: DELETE removes all rows (0 or more) → Envelope with code=200000."""


@endpoint_test(
    method="DELETE",
    path="/api/v1/collaboration/tasks/discovery/tasks",
    scenario="error",
    seed=_seed_clear_tasks_error,
    expect=ExpectError(status=500),
)
def clear_tasks_err_db_unavailable():
    """Error path: DatabasePlugin.transactional_orm_session raises → InternalError → 500."""
