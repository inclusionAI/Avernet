"""bot_collaborator API-lifecycle business flows — data, not tests.

Single source of truth for both executors:
  - 路 A: tests/e2e/test_bot_collaborator_flows.py via flow_runner.run_flow
  - 路 B: tests/acceptance/bot_collaborator/ via flow_runner_live.run_flow_live
and for the E3 coverage guard.

bot_collaborator manages service-Bot collaborators (CRUD + permission levels
OWNER/ADMIN/MEMBER/NONE) and a per-bot collaboration edit lock (acquire/
release with reentry). It depends on 3 unified ORM repositories
(Collaborator / BotCollabLock / BotCollabLog) + the existing BotRepository,
all running the same code body on SqliteDB and ZdasDB.

Two LOCAL exemptions documented in
docs/singlebox-eval/findings/bot_collaborator-passport-and-concurrency.md:
  - AgentPass admin sync: LocalPassportPlugin is logging no-op. Tests assert
    the sync call does not raise; real AgentPass writes only verifiable in prod.
  - Cross-process lock concurrency: SQLite StaticPool single-connection
    can't physically reproduce multi-writer contention; reentrant single-
    process path is covered, multi-process is exempt.

LOCAL-reachable read paths covered by FlowCases:
  - GET /api/bot/collaborator/list (no bot → success=False/404)
  - POST /api/bot/collaborator/check_permission (no bot → success=False/404)
  - GET /api/bot/collaborator/lock/info (no lock held → success=True)

The collaborator add→list→remove round-trip and lock acquire→release
lifecycle are NOT FlowCases (they require seeded ac_bots row + chained
mutation state); exercised in the e2e test's separate helpers.

Wire envelope (probe-confirmed):
  - Standard ApiResponse: {success, message, error_code, data}
  - Not-found shape: {success: False, error_code: 404, data: None,
                       message: "Bot 不存在: ..."}
  - Lock endpoints do NOT validate bot existence — they key on
    `bot_id:owner_id` directly.

Each FlowCase covers=["bot_collaborator"]. Auth via x-user-id; route-A
default "e2e_user", route-B default "e2e_lifecycle_user" — flow expects
pin literal values matching route-A.

Gotcha: FlowRunner.run_flow does NOT interpolate `expect` (only path/body).
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

BOT_COLLABORATOR_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: list collaborators for a non-existent bot. Probe-confirmed:
    # envelope-200 failure {success: False, error_code: 404, data: None}.
    FlowCase(
        name="bot_collaborator-list-no-bot",
        covers=["bot_collaborator"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            FlowStep(
                method="GET",
                path="/api/bot/collaborator/list?bot_id=bot_e2e_collab&owner_id=e2e_user",
                expect_status=200,
                expect={"success": False, "error_code": 404, "data": None},
            ),
        ],
    ),
    # Flow 2: check_permission for a non-existent bot. Same not-found envelope.
    # (Note: the original plan referenced /list-user and /permission paths
    # which don't exist — actual endpoints are /check_permission only.
    # list_user_collaborations is service-level, not exposed via HTTP.)
    FlowCase(
        name="bot_collaborator-check-permission-no-bot",
        covers=["bot_collaborator"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/bot/collaborator/check_permission",
                body={"bot_id": "bot_e2e_collab", "owner_id": "e2e_user",
                      "user_id": "e2e_user"},
                expect_status=200,
                expect={"success": False, "error_code": 404, "data": None},
            ),
        ],
    ),
    # Flow 3: lock info for a bot with no prior acquire. Probe-confirmed lock
    # endpoints DON'T validate bot existence — they key purely on
    # `bot_id:owner_id`. With no prior acquire on this unique bot_id, lock
    # state is "not held" — assert success=True only (data shape verified
    # by the e2e test's stateful lock-lifecycle helper).
    FlowCase(
        name="bot_collaborator-lock-info-not-held",
        covers=["bot_collaborator"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/bot/collaborator/lock/info?bot_id=bot_e2e_collab_unlocked&owner_id=e2e_user",
                expect_status=200,
                expect={"success": True},
            ),
        ],
    ),
]
