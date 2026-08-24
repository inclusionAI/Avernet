"""Endpoint tests for task-discovery public routes.

Covers:
- POST /api/v1/collaboration/tasks/discovery/discover (happy + error)
- GET  /api/v1/collaboration/tasks/discovery/status  (happy + error)

Happy cases assert only ``{"code": 200000}`` — the handler returns the
unified success ``Envelope`` when the discovery flow runs end-to-end,
regardless of whether the DB file exists or individual task sessions
fail (session-creation errors are captured per-task, not at the top
level).  This makes the cases robust in both CI (no DB file → empty
discoveries) and local dev (DB file present with seed data).

The POST error case is a FastAPI 422 (missing required bot_id).
The GET error case points the DB path at a directory, which makes
sqlite3.connect() raise OperationalError outside the reader's inner
try/except; the handler re-raises it as ``InternalError`` → the app's
``DomainError`` handler answers a 500 ``ErrorEnvelope``.
"""
from __future__ import annotations

import os
from pathlib import Path

from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryResult,
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    init_discovered_tasks_db,
)
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


# The error case for GET /status: point TASK_DISCOVERY_DATA_FILE at a
# directory. Path.exists() returns True for a directory, so
# SqliteTaskReader proceeds to sqlite3.connect() — which is OUTSIDE
# the reader's inner try/except. connect() on a directory raises
# sqlite3.OperationalError, propagating to the handler's except-Exception,
# which re-raises it as InternalError → 500 ErrorEnvelope.
_ERROR_DB_DIR = os.path.join(
    os.environ.get("TMPDIR", "/tmp"),
    "td_status_err_dir",
)


def _seed_status_error_dir(world) -> None:
    """Create a directory at the error DB path and set the env var."""
    os.makedirs(_ERROR_DB_DIR, exist_ok=True)
    os.environ["TASK_DISCOVERY_DATA_FILE"] = _ERROR_DB_DIR


def _cleanup_status_error_env(response, world) -> None:
    """Restore TASK_DISCOVERY_DATA_FILE and remove the temp directory."""
    os.environ.pop("TASK_DISCOVERY_DATA_FILE", None)
    try:
        os.rmdir(_ERROR_DB_DIR)
    except OSError:
        pass


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/discovery/status",
    scenario="err_db_path_is_directory",
    seed=_seed_status_error_dir,
    extra_assertions=(_cleanup_status_error_env,),
    expect=ExpectError(status=500),
)
def get_status_err_db_path_is_directory():
    """Error path: DB path is a directory -> sqlite3.connect raises -> InternalError -> 500."""


# Exercise the status aggregation loop with both a task that has an in-memory
# discovery result and one that has not been discovered in this process.
_STATUS_COVERAGE_DB = Path(
    os.environ.get("TMPDIR", "/tmp"),
    "task_discovery_status_coverage.db",
)
_STATUS_DISCOVERED_TASK_ID = "status-discovered"
_STATUS_PENDING_TASK_ID = "status-pending"


def _status_task(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "bot_id": "status-bot",
        "owner_id": "status-owner",
        "dt": "2026-08-20",
        "project_name": task_id,
        "description": "status coverage",
        "business_scenario": "testing",
        "discovery_basis": "changed-line coverage",
        "priority": "medium",
        "status": "pending_confirmation",
    }


def _seed_status_with_discovered_and_pending_tasks(world) -> None:
    init_discovered_tasks_db(
        _STATUS_COVERAGE_DB,
        [
            _status_task(_STATUS_DISCOVERED_TASK_ID),
            _status_task(_STATUS_PENDING_TASK_ID),
        ],
    )
    os.environ["TASK_DISCOVERY_DATA_FILE"] = str(_STATUS_COVERAGE_DB)
    service = world.get(DiscoveryService)
    task = DiscoveredTask(**_status_task(_STATUS_DISCOVERED_TASK_ID))
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
    os.environ.pop("TASK_DISCOVERY_DATA_FILE", None)
    _STATUS_COVERAGE_DB.unlink(missing_ok=True)


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
