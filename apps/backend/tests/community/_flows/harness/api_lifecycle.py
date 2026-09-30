"""harness API-lifecycle business flows — data, not tests.

Single source of truth for both executors:
  - 路 A: tests/e2e/test_harness_flows.py via flow_runner.run_flow
  - 路 B: tests/acceptance/harness/ via flow_runner_live.run_flow_live
and for the E3 coverage guard.

harness is Bot health-check domain (scan → plan patch → apply/rollback). 4
repositories (HarnessTemplate / HarnessPatch / HarnessPatchRecord /
HarnessScanRecord) are unified ORM impl running on SqliteDB and ZdasDB with
the same code body.

Single box covers ONLY the read-only CRUD paths. POST /diagnose /
/generate-patches / /apply / /rollback are NOT exercised — they depend on
LLM (antchat HTTP, disabled in LOCAL without token) or Arca file ops (LOCAL
no arca binding). See
docs/singlebox-eval/findings/harness-llm-and-arca-unmocked.md.

LOCAL-reachable read paths covered here:
  - GET /api/harness/templates (list — flat envelope {success, total, items[]})
  - GET /api/harness/templates/{int_id} (get — missing → raw 404 {detail})
  - GET /api/harness/patches?bot_id=&entity_id= (list — key 'templates' NOT 'items'!)
  - GET /api/harness/patch-records?bot_id=&entity_id= (list — key 'items', no total)
  - GET /api/harness/diagnose/records?bot_id=&entity_id= (list — paginated total/page/size/items)

Wire envelope shapes (probe-confirmed, NOT uniform):
  - /templates list: {success, total, items}
  - /templates/{id} missing: 404 raw {detail: "Template <id> not found"}
  - /patches list: {success, bot_id, entity_id, templates}  ← key is 'templates'!
  - /patch-records list: {success, bot_id, entity_id, items}
  - /diagnose/records list: {success, total, page, size, items}

entity_id is REQUIRED on patches/patch-records/diagnose-records — without it
the endpoint returns 400 raw or 422 pydantic.

Each FlowCase covers=["harness"]. Auth via x-user-id; route-A default
"e2e_user", route-B default "e2e_lifecycle_user" — flow expects pin literal
values matching route-A; route-B test passes default_headers={"x-user-id":
"e2e_user"} to override. entity_id query param matches the auth identity
("e2e_user") so seeded round-trips in Task 2 see their own data.

Gotcha: FlowRunner.run_flow does NOT interpolate `expect` (only path/body).
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

HARNESS_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: templates list with no seed. Probe-confirmed envelope
    # {success, total, items} — flat, no `data` wrapper.
    FlowCase(
        name="harness-templates-list-empty",
        covers=["harness"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            FlowStep(
                method="GET",
                path="/api/harness/templates",
                expect_status=200,
                expect={"success": True, "total": 0, "items": []},
            ),
        ],
    ),
    # Flow 2: get template by valid-but-missing integer id. Probe-confirmed
    # the router raises FastAPI HTTPException → raw 404 (NOT envelope).
    FlowCase(
        name="harness-template-get-missing",
        covers=["harness"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/harness/templates/999999",
                expect_status=404,
                expect={"detail": "Template 999999 not found"},
            ),
        ],
    ),
    # Flow 3: patches list with no seed, scoped to bot_id+entity_id.
    # Probe-confirmed list key is `templates` (NOT `items`!), echoes
    # bot_id/entity_id, no `total`.
    FlowCase(
        name="harness-patches-list-empty",
        covers=["harness"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/harness/patches?bot_id=bot_e2e_harness&entity_id=e2e_user",
                expect_status=200,
                expect={"success": True,
                        "bot_id": "bot_e2e_harness",
                        "entity_id": "e2e_user",
                        "templates": []},
            ),
        ],
    ),
    # Flow 4: patch-records list with no seed. Probe-confirmed envelope
    # {success, bot_id, entity_id, items} — list key `items`, no `total`.
    FlowCase(
        name="harness-patch-records-list-empty",
        covers=["harness"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/harness/patch-records?bot_id=bot_e2e_harness&entity_id=e2e_user",
                expect_status=200,
                expect={"success": True,
                        "bot_id": "bot_e2e_harness",
                        "entity_id": "e2e_user",
                        "items": []},
            ),
        ],
    ),
    # Flow 5: diagnose records list with no seed. Probe-confirmed paginated
    # envelope {success, total, page, size, items}.
    FlowCase(
        name="harness-diagnose-records-list-empty",
        covers=["harness"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/harness/diagnose/records?bot_id=bot_e2e_harness&entity_id=e2e_user",
                expect_status=200,
                expect={"success": True, "total": 0,
                        "page": 1, "size": 20, "items": []},
            ),
        ],
    ),
]
