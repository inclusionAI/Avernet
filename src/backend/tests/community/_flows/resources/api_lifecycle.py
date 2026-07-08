"""resources API-lifecycle business flows — data, not tests.

Single source of truth for both executors:
  - 路 A: tests/e2e/test_resources_flows.py via flow_runner.run_flow (TestClient)
  - 路 B: tests/acceptance/resources/ via flow_runner_live.run_flow_live (real backend)
and for the E3 coverage guard (tests/architecture/test_e2e_module_coverage.py).

LOCAL+SQLITE, mock-free for the DB + daas filesystem path. The 3 external-dep
paths are deliberately NOT exercised (see
docs/singlebox-eval/findings/resources-external-deps-unmocked.md):
  - device_provider="arca" → bot has no arca binding in LOCAL → falls through
    to daas branch naturally
  - link_type="yuque" → flow only creates link_type="dima" links
  - publish sandbox_id path → flow doesn't pass publish_id

Each FlowCase covers=["resources"]. Auth via x-user-id; route-A default
"e2e_user", route-B default "e2e_lifecycle_user". Path params on file ops:
bot_id + entity_id + entity_type query.

Gotchas (probe-confirmed shapes):
  - FlowRunner.run_flow does NOT interpolate `expect` (only path/body) — use
    LITERAL values in expect.
  - /files/mkdir uses fastapi.Form(path=...) not JSON — CANNOT be a FlowStep
    (FlowStep body is JSON-encoded). Exercised via the e2e test's helper.
  - /files/upload is multipart — same limitation, also via test helper.
  - /files response envelope is {success, path, items} — `items` NOT `data`.
  - URL/Node duplicate returns 409 with {detail: ...} (FastAPI default), NOT
    {success, data} — so 409 steps have NO expect body, only expect_status=409.
  - Timestamps in responses are inconsistent across endpoints — do NOT pin them.
  - Resource id is a sequential string ("1", "2", ...) — extract but don't pin.
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep

RESOURCES_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: URL resource CRUD — create → check-name (duplicate detected) →
    # 409 on duplicate create. Pure DB; unified ORM repo round-trips through
    # SQLite. Extract id from create, prove it's a non-empty string by chaining
    # the check-name into a subsequent state.
    FlowCase(
        name="resources-url-resource-crud",
        covers=["resources"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            FlowStep(
                method="POST",
                path="/api/resources/url?bot_id=bot_e2e_res",
                body={"name": "E2E URL", "url": "https://e2e.example.com"},
                expect_status=200,
                expect={"success": True,
                        "data": {"name": "E2E URL", "resource_type": "url",
                                 "status": "active", "url": "https://e2e.example.com"}},
                extract={"url_resource_id": "data.id"},
            ),
            FlowStep(
                method="GET",
                path="/api/resources/check-name?name=E2E URL&resource_type=url&bot_id=bot_e2e_res",
                expect_status=200,
                expect={"success": True, "data": {"available": False}},
            ),
            # Duplicate name → 409 with FastAPI's default {detail: ...} envelope
            # (no success/data wrapping). Pin status only.
            FlowStep(
                method="POST",
                path="/api/resources/url?bot_id=bot_e2e_res",
                body={"name": "E2E URL", "url": "https://e2e.example.com/v2"},
                expect_status=409,
            ),
        ],
    ),
    # Flow 2: Node resource CRUD + list scoping by bot_id.
    # Extract id from create, then list scoped to same bot_id + resource_type=node,
    # and re-extract the first list item's id. Belt-and-suspenders on chain proof
    # left to the e2e test wrapper.
    FlowCase(
        name="resources-node-resource-crud",
        covers=["resources"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/resources/node?bot_id=bot_e2e_res_n",
                body={"name": "E2E Node", "node_address": "ipfs://e2e-node"},
                expect_status=200,
                expect={"success": True,
                        "data": {"name": "E2E Node", "resource_type": "node",
                                 "node_address": "ipfs://e2e-node"}},
                extract={"node_resource_id": "data.id"},
            ),
            # List scoped to same bot_id, filter by resource_type=node.
            # extract first listed id; e2e test asserts it equals node_resource_id.
            FlowStep(
                method="GET",
                path="/api/resources?bot_id=bot_e2e_res_n&owner_id=e2e_user&resource_type=node",
                expect_status=200,
                expect={"success": True},
                extract={"first_listed_id": "data.0.id"},
            ),
        ],
    ),
    # Flow 3: file daas list endpoint envelope pin.
    # NOTE on what's NOT in this flow:
    #   - mkdir uses fastapi.Form(path=...) NOT JSON — FlowStep body= is
    #     JSON-encoded so we can't POST to /files/mkdir through FlowRunner.
    #     Exercised in the e2e test's separate multipart helper.
    #   - upload is multipart (file=) — same FlowRunner limitation.
    # What this flow pins:
    #   - GET /files returns the file_router envelope shape {success, path, items}
    #     (NOT the {success, data} ResourceListResponse — file_router uses
    #     FileListResponse). LOCAL device_provider != arca, so file_router falls
    #     through to the daas (real-fs) branch.
    # The flow uses a unique bot_id so its list is empty regardless of host fs
    # state from prior runs (different bot dirs are isolated by path_factory).
    FlowCase(
        name="resources-file-daas-list",
        covers=["resources"],
        steps=[
            FlowStep(
                method="GET",
                path="/api/resources/files?bot_id=bot_e2e_res_f&entity_id=e2e_user&entity_type=staff",
                expect_status=200,
                expect={"success": True, "items": []},
            ),
        ],
    ),
]
