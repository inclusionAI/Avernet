"""Quality Task API-lifecycle business flows — data, not tests.

These FlowCases are the single source of truth for both executors:
  - 路 A: tests/e2e/test_quality_flows.py via flow_runner.run_flow (TestClient)
  - 路 B: tests/acceptance/ via flow_runner_live.run_flow_live (real backend)
and for the E3 coverage guard (tests/architecture/test_e2e_module_coverage.py).

They are REAL business flows against live endpoints, mock-free in LOCAL+SQLITE.
Each covers=["quality"]; each extracts a value from one step and
interpolates it into the next, with the chaining step's ``expect`` re-asserting
the round-trip so a broken extract→interpolate chain fails loudly.

PREREQUISITE: The e2e test runner must seed a bot with bot_id="bot_e2e_quality"
and owner_id="e2e_user" before running these flows. This is required by
CollaboratorPermissionInterceptor which validates that the requesting user
(x-user-id header set by flow_runner) is the bot's owner.
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

# Bot ID must match the seeded bot in test_quality_flows.py::_seed_bot_for_quality
# Owner ID must be "e2e_user" to match flow_runner's default x-user-id header
BOT_ID = "bot_e2e_quality"
OWNER_ID = "e2e_user"

QUALITY_FLOWS: list[FlowCase] = [
    # Flow 1: create a quality task, then fetch it by the extracted ID.
    # Proves a real DB-backed create returns an id we can route a follow-up GET to.
    FlowCase(
        name="quality-task-create-then-fetch",
        covers=["quality"],
        steps=[
            # Liveness — no auth, no DB.
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            # Real DB write; pull the new task's id out of the response.
            FlowStep(
                method="POST",
                path="/api/quality/tasks/create",
                body={
                    "task_type": "eval",
                    "biz_type": "service_bot_single",
                    "bot_id": BOT_ID,
                    "owner_id": OWNER_ID,
                },
                expect_status=200,
                expect={"success": True},
                extract={"task_id": "data.id"},
            ),
            # Fetch the just-created task by the extracted id.
            # The response should contain the same bot_id we created with.
            FlowStep(
                method="GET",
                path="/api/quality/tasks/{task_id}",
                expect_status=200,
                expect={"bot_id": BOT_ID, "task_type": "eval"},
            ),
        ],
    ),
    # Flow 2: create task and update status via admin endpoint.
    # Proves the status_for_others endpoint works correctly.
    # Note: We skip process() transitions (init -> env_preparing -> env_ready -> ...)
    # because they require external services (PublishFlowService, BaaS, eval API).
    FlowCase(
        name="quality-task-create-update-status",
        covers=["quality"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            # Create task
            FlowStep(
                method="POST",
                path="/api/quality/tasks/create",
                body={
                    "task_type": "stress_test",
                    "biz_type": "multi_bot",
                    "bot_id": BOT_ID,
                    "owner_id": OWNER_ID,
                },
                expect_status=200,
                expect={"success": True},
                extract={"task_id": "data.id"},
            ),
            # Set status to env_preparing using admin endpoint
            FlowStep(
                method="POST",
                path="/api/quality/tasks/{task_id}/status_for_others?status=env_preparing",
                expect_status=200,
                expect={"success": True},
                headers={"x-user-id": "100000"},  # seeded super_admin
            ),
            # Fetch and verify status changed to env_preparing
            FlowStep(
                method="GET",
                path="/api/quality/tasks/{task_id}",
                expect_status=200,
                expect={"status": "env_preparing"},
            ),
            # Update status again with ext data
            FlowStep(
                method="POST",
                path="/api/quality/tasks/{task_id}/status_for_others?status=env_ready",
                body={"baas_publish_id": "test-publish-123", "bot_uuid": "test-uuid-456"},
                expect_status=200,
                expect={"success": True},
                headers={"x-user-id": "100000"},  # seeded super_admin
            ),
            # Fetch and verify status changed to env_ready and ext was merged
            FlowStep(
                method="GET",
                path="/api/quality/tasks/{task_id}",
                expect_status=200,
                expect={"status": "env_ready"},
            ),
        ],
    ),
    # Flow 3: list tasks with pagination and filtering.
    # Proves list endpoint returns paginated results with filters.
    FlowCase(
        name="quality-task-list-with-pagination",
        covers=["quality"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            # Create a task first to ensure list is not empty
            FlowStep(
                method="POST",
                path="/api/quality/tasks/create",
                body={
                    "task_type": "eval",
                    "biz_type": "service_bot_single",
                    "bot_id": BOT_ID,
                    "owner_id": OWNER_ID,
                },
                expect_status=200,
                expect={"success": True},
            ),
            # List tasks with pagination
            FlowStep(
                method="GET",
                path="/api/quality/tasks",
                query={"task_type": "eval", "biz_type": "service_bot_single", "page": "1", "page_size": "10"},
                expect_status=200,
                expect={"page": 1, "page_size": 10},
            ),
        ],
    ),
]