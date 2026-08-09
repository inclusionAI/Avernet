"""bot_management API-lifecycle business flows — data, not tests.

Single source of truth for both executors:
  - 路 A: tests/community/e2e/test_bot_management_flows.py via flow_runner.run_flow
  - 路 B: tests/community/acceptance/bot_management/ via flow_runner_live.run_flow_live
and for the E3 coverage guard.

bot_management is the largest backend module (30+ HTTP endpoints, 3000+ line
BotService). The quick route-A tests keep the DB-only read paths cheap, while
live-only flows exercise the real singlebox chain:

    Backend -> LocalDeviceService -> BaaS -> OpenClaw local runtime

The created bot still reports device_provider="local" in singlebox; that value
is a transitional provider fact, and the underlying allocation/release is done
by BaaS. Provider-specific arca/baas/teclaw read branches are covered in the
acceptance layer with explicit local SQL seeds after the live backend starts.

LOCAL-reachable read paths covered here:
  - GET /api/bots (list — DB CRUD via BotRepository on SQLite)
  - GET /api/bots/check/name?bot_name=... (availability check)
  - GET /api/bots/{bot_id} (get — envelope-wrapped 404 for missing)
  - live POST /api/bots, status polling, list/search/detail/update/config/ext

Each FlowCase covers=["bot_management"]. Auth via x-user-id; route-A default
"e2e_user", route-B default "e2e_lifecycle_user" — flow expects pin literal
values matching route-A; route-B test passes default_headers={"x-user-id":
"e2e_user"} to override.

Gotcha: FlowRunner.run_flow does NOT interpolate `expect` (only path/body).
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

BOT_MANAGEMENT_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: list with no seed. Wire envelope (Task 0 probe-confirmed):
    # {success: True, error_code: 200, data: {total: 0, items: []}}.
    # Envelope key is `items` (NOT bots/list/results).
    FlowCase(
        name="bot_management-list-empty",
        covers=["bot_management"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            FlowStep(
                method="GET",
                path="/api/bots",
                query={"entity_id": "e2e_user", "entity_type": "staff"},
                expect_status=200,
                expect={"success": True, "error_code": 200,
                        "data": {"total": 0, "items": []}},
            ),
        ],
    ),
    # Flow 2: check-name with a name that's definitely not taken. Pins LOCAL
    # availability contract — exists=False, bot_name echoed.
    FlowCase(
        name="bot_management-check-name-available",
        covers=["bot_management"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/bots/check/name?bot_name=NonExistent_E2E_Bot",
                expect_status=200,
                expect={"success": True,
                        "data": {"exists": False, "bot_name": "NonExistent_E2E_Bot"}},
            ),
        ],
    ),
    # Flow 3: get a non-existent bot_id. Task 0 probe confirmed router returns
    # envelope-200 + success=False + error_code=404 + data=None (NOT raw 404).
    FlowCase(
        name="bot_management-get-bot-missing",
        covers=["bot_management"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/bots/bot_does_not_exist_e2e",
                expect_status=200,
                expect={"success": False, "error_code": 404, "data": None},
            ),
        ],
    ),
    # Flow 4: live singlebox owner lifecycle. This is intentionally live-only
    # because POST /api/bots waits on a real BaaS-backed local device.
    FlowCase(
        name="bot_management-live-owner-crud-baas-backed",
        covers=["bot_management"],
        live_only=True,
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            FlowStep(
                method="POST",
                path="/api/bots",
                body={
                    "bot_name": "{bot_name}",
                    "bot_desc": "created by singlebox bot_management coverage",
                    "entity_id": "{user_id}",
                    "entity_type": "staff",
                    "engine_type": "openclaw",
                    "bot_type": "personal",
                },
                expect_status=200,
                expect={"success": True},
                extract={
                    "bot_id": "data.bot.bot_id",
                    "owner_id": "data.bot.owner_id",
                    "entity_id": "data.bot.entity_id",
                },
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/status",
                expect_status=200,
                expect={
                    "success": True,
                    "data": {
                        "is_ready": True,
                        "device_provider": "local",
                    },
                },
                poll_timeout_sec=180,
                poll_interval_sec=2,
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots",
                query={"entity_id": "{entity_id}", "entity_type": "staff"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/by-owner",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/by-owner-or-collaborator",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/check/name",
                query={"bot_name": "{bot_name}"},
                expect_status=200,
                expect={"success": True, "data": {"exists": True}},
            ),
            FlowStep(
                method="POST",
                path="/api/bots/search",
                body={
                    "key": "BotMgmt Live Owner",
                    "owner_id": "{owner_id}",
                    "bot_type": "personal",
                    "page": 1,
                    "page_size": 10,
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/search/domain-bots",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/ceiling",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/passport",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/bots/passport/refresh-token",
                body={
                    "bot_id": "{bot_id}",
                    "owner_workno": "{owner_id}",
                    "token": "singlebox-refreshed-passport-token",
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/appcoding-bots",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/work-dir",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/config-dir",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/work-dir?engine_type=claude_code",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/config-dir?engine_type=claude_code",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="PUT",
                path="/api/bots/{bot_id}/engine-config",
                body={"singlebox": {"module": "bot_management", "case": "owner-crud"}},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/engine-config",
                expect_status=200,
                expect={"success": True, "data": {"singlebox": {"module": "bot_management"}}},
            ),
            FlowStep(
                method="PATCH",
                path="/api/bots/{bot_id}/ext",
                body={"singlebox_bot_management": "covered"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="PUT",
                path="/api/bots/{bot_id}",
                body={
                    "bot_name": "BotMgmt Live Owner Bot Updated",
                    "bot_desc": "updated by singlebox bot_management coverage",
                    "ext": {"singlebox_update": True},
                },
                expect_status=200,
                expect={"success": True, "data": {"bot_name": "BotMgmt Live Owner Bot Updated"}},
            ),
            FlowStep(
                method="POST",
                path="/api/bots/{bot_id}/restart",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/status",
                expect_status=200,
                expect={
                    "success": True,
                    "data": {
                        "is_ready": True,
                        "device_provider": "local",
                    },
                },
                poll_timeout_sec=180,
                poll_interval_sec=2,
            ),
            FlowStep(
                method="POST",
                path="/api/bots",
                body={
                    "bot_name": "{delete_bot_name}",
                    "bot_desc": "created only to verify user-owned delete",
                    "entity_id": "{user_id}",
                    "entity_type": "staff",
                    "engine_type": "openclaw",
                    "bot_type": "personal",
                },
                expect_status=200,
                expect={"success": True},
                extract={"delete_bot_id": "data.bot.bot_id"},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{delete_bot_id}/status",
                expect_status=200,
                expect={
                    "success": True,
                    "data": {
                        "is_ready": True,
                        "device_provider": "local",
                    },
                },
                poll_timeout_sec=180,
                poll_interval_sec=2,
            ),
            FlowStep(
                method="DELETE",
                path="/api/bots/{delete_bot_id}",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/bots",
                body={
                    "bot_name": "{service_bot_name}",
                    "bot_desc": "service bot created by singlebox bot_management coverage",
                    "entity_id": "{user_id}",
                    "entity_type": "staff",
                    "engine_type": "openclaw",
                    "bot_type": "service",
                },
                expect_status=200,
                expect={"success": True},
                extract={"service_bot_id": "data.bot.bot_id"},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{service_bot_id}/status",
                expect_status=200,
                expect={
                    "success": True,
                    "data": {
                        "is_ready": True,
                        "device_provider": "local",
                    },
                },
                poll_timeout_sec=180,
                poll_interval_sec=2,
            ),
            FlowStep(
                method="GET",
                path="/api/bots/by-owner",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{service_bot_id}",
                expect_status=200,
                expect={"success": True, "data": {"bot_type": "service"}},
            ),
            FlowStep(
                method="POST",
                path="/api/bots/passport/refresh-token",
                body={
                    "bot_id": "{service_bot_id}",
                    "owner_workno": "{owner_id}",
                    "token": "singlebox-service-refreshed-passport-token",
                },
                expect_status=200,
                expect={"success": True, "error_code": 200},
            ),
        ],
    ),
]
