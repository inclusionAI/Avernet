"""Endpoint tests for task-discovery public routes.

Covers:
- POST /openapi/v1/collaboration/tasks/discovery/discover (happy + error)
- GET  /openapi/v1/collaboration/tasks/discovery/status  (happy + error)

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

from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

# ---- POST /openapi/v1/collaboration/tasks/discovery/discover ----

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/discovery/discover",
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
    path="/openapi/v1/collaboration/tasks/discovery/discover",
    scenario="err_missing_bot_id",
    input=CaseInput(query_params={"owner_id": "td-owner-1"}),
    expect=ExpectError(status=422),
)
def discover_tasks_err_missing_bot_id():
    """Error path: missing required bot_id -> FastAPI 422."""


# ---- GET /openapi/v1/collaboration/tasks/discovery/status ----

@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/discovery/status",
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
    path="/openapi/v1/collaboration/tasks/discovery/status",
    scenario="err_db_path_is_directory",
    seed=_seed_status_error_dir,
    extra_assertions=(_cleanup_status_error_env,),
    expect=ExpectError(status=500),
)
def get_status_err_db_path_is_directory():
    """Error path: DB path is a directory -> sqlite3.connect raises -> InternalError -> 500."""
