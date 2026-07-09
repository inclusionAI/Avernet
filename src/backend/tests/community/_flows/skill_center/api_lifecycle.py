"""skill_center API-lifecycle business flows — data, not tests.

These FlowCases are the single source of truth for both executors:
  - 路 A: tests/community/e2e/test_skill_center_flows.py via flow_runner.run_flow (TestClient)
  - 路 B: tests/community/acceptance/ via flow_runner_live.run_flow_live (real backend)
and for the E3 coverage guard (tests/architecture/test_e2e_module_coverage.py).

They are REAL business flows against live endpoints, mock-free in LOCAL+SQLITE.
Each covers=["skill_center"]; each extracts a value from one step and
interpolates it into the next, with the chaining step's ``expect`` re-asserting
the round-trip so a broken extract→interpolate chain fails loudly.

(The dual-skills-dir caveat and the route-B physical-artifact flow live in
hot_reload_lifecycle.py; these are API-level round-trips only.)
"""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowFile, FlowStep

API_LIFECYCLE_FLOWS: list[FlowCase] = [
    # Flow 1: create a category, then fetch skills under it by the extracted
    # code interpolated into the path. Proves a real DB-backed create returns
    # an id we can route a follow-up GET to.
    FlowCase(
        name="skill_center-category-create-then-fetch",
        covers=["skill_center"],
        steps=[
            # Liveness — no auth, no DB.
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            # Real DB write; pull the new category's code out of the response.
            FlowStep(
                method="POST",
                path="/api/skill-categories",
                body={"code": "cat_e2e_create_fetch", "name": "E2E Create-Fetch"},
                expect_status=200,
                expect={"success": True, "data": {"code": "cat_e2e_create_fetch", "level": 0}},
                extract={"cat_code": "data.code"},
            ),
            # Fetch skills under the just-created category by the extracted code.
            # New category has no skills yet — data must be empty list, total 0.
            FlowStep(
                method="GET",
                path="/api/skill-categories/{cat_code}/skills",
                expect_status=200,
                expect={"success": True, "total": 0, "data": []},
            ),
            FlowStep(
                method="GET",
                path="/api/skill-categories",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skill-categories/tree",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="PUT",
                path="/api/skill-categories/{cat_code}",
                body={"name": "E2E Create-Fetch Updated", "sort_order": 3},
                expect_status=200,
                expect={"success": True, "data": {"name": "E2E Create-Fetch Updated"}},
            ),
            FlowStep(
                method="DELETE",
                path="/api/skill-categories/{cat_code}",
                expect_status=200,
                expect={"success": True, "data": {"status": 0}},
            ),
        ],
    ),
    # Flow 2: create a root category, then a child referencing it via
    # parent_code interpolated from step 1. The child's expect re-asserts
    # data.parent_code == the extracted code — a broken chain fails loudly.
    FlowCase(
        name="skill_center-category-parent-child",
        covers=["skill_center"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/skill-categories",
                body={"code": "cat_e2e_parent", "name": "E2E Parent"},
                expect_status=200,
                expect={"success": True, "data": {"code": "cat_e2e_parent"}},
                extract={"parent_code": "data.code"},
            ),
            FlowStep(
                method="POST",
                path="/api/skill-categories",
                body={"code": "cat_e2e_child", "name": "E2E Child", "parent_code": "{parent_code}"},
                expect_status=200,
                expect={"success": True, "data": {"parent_code": "cat_e2e_parent", "level": 1}},
            ),
        ],
    ),
    # Flow 3: a real skillsets WRITE flow. Create a skill set, pull its id out
    # of data.id, then GET it back by that id and re-assert the id round-trips.
    # POST /api/skillsets is gated by CollaboratorPermissionInterceptor
    # (owner_id="$request.user_id"): since body.user_id == the LOCAL identity
    # staffId (x-user-id, injected by the runner), the caller is OWNER and is
    # let through with zero seed. The GET detail endpoint has no interceptor.
    #
    # Unlike Flow 2 (where the code is caller-supplied, so expect can echo it as
    # a literal), the skill set id is DB-auto-increment — unpredictable at
    # declaration time and create body has no id field. So the round-trip is
    # made EXPLICIT, not implicit: GET re-extracts data.id into fetched_id and
    # the runner asserts fetched_id == skill_set_id. (FlowRunner expect is not
    # interpolated, so it can't carry the dynamic id; the equality check does.)
    FlowCase(
        name="skill_center-skillset-create-then-fetch",
        covers=["skill_center"],
        steps=[
            FlowStep(method="GET", path="/api/health", expect_status=200,
                     expect={"status": "ok"}),
            FlowStep(
                method="POST",
                path="/api/skillsets",
                body={"name": "E2E SkillSet", "user_id": "e2e_user"},
                expect_status=200,
                expect={"success": True, "data": {"name": "E2E SkillSet"}},
                extract={"skill_set_id": "data.id"},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/{skill_set_id}",
                expect_status=200,
                expect={"success": True, "data": {"name": "E2E SkillSet"}},
                extract={"fetched_id": "data.id"},
            ),
        ],
    ),
    # Flow 4: create bot → skill set → git skill, then exercise the read/update
    # surfaces that are safe in live singlebox and do not require multipart.
    FlowCase(
        name="skill_center-skill-and-skillset-crud-roundtrip",
        covers=["skill_center"],
        live_only=True,
        steps=[
            FlowStep(
                method="POST",
                path="/api/bots",
                body={"bot_name": "API Lifecycle Bot", "bot_type": "personal"},
                expect_status=200,
                extract={"user_id": "data.bot.owner_id", "bot_id": "data.bot.bot_id"},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/status",
                expect_status=200,
                expect={"success": True, "data": {"is_ready": True}},
                poll_timeout_sec=180,
                poll_interval_sec=2,
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets/default/ensure",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/default/current",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets",
                body={
                    "name": "API Lifecycle SkillSet",
                    "description": "created by singlebox API lifecycle",
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={"success": True, "data": {"name": "API Lifecycle SkillSet"}},
                extract={"skill_set_id": "data.id"},
            ),
            FlowStep(
                method="POST",
                path="/api/skills",
                body={
                    "name": "brainstorming",
                    "description": "created by singlebox API lifecycle",
                    "git_path": "git://infra/common/brainstorming",
                    "category": "crud",
                    "tags": ["singlebox", "coverage"],
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={"success": True, "data": {"name": "brainstorming"}},
                extract={
                    "skill_id": "data.id",
                    "link_name": "data.link_name",
                },
            ),
            FlowStep(
                method="GET",
                path="/api/skills",
                query={"category": "crud", "bot_id": "{bot_id}", "limit": "10"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{skill_id}",
                expect_status=200,
                expect={"success": True, "data": {"name": "brainstorming"}},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{link_name}/id",
                expect_status=200,
                expect={"success": True, "name": "brainstorming"},
            ),
            FlowStep(
                method="PUT",
                path="/api/skills/{skill_id}",
                body={
                    "description": "updated by singlebox API lifecycle",
                    "tags": ["singlebox", "coverage", "updated"],
                    "user_id": "{user_id}",
                },
                expect_status=200,
                expect={
                    "success": True,
                    "data": {
                        "description": "updated by singlebox API lifecycle",
                        "tags": ["singlebox", "coverage", "updated"],
                    },
                },
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{skill_id}/readme",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skill/permission",
                query={"skill_id": "{skill_id}"},
                expect_status=200,
                expect={"success": True, "authorized": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skill/permission/apply",
                body={"skill_id": "{skill_id}", "reason": "singlebox coverage"},
                expect_status=200,
                expect={"success": True, "all_authorized": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{skill_id}/parameters",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                body={"parameters": {"mode": "singlebox", "enabled": True}},
                expect_status=200,
                expect={"success": True, "data": {"saved": True}},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{skill_id}/parameters",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{skill_id}/members",
                expect_status=200,
                expect={"success": True, "count": 0},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{skill_id}/members",
                body={"user_id": "skill_member_one", "role": "member"},
                expect_status=200,
                expect={"success": True, "data": {"user_id": "skill_member_one", "role": "member"}},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{skill_id}/members/batch",
                body={"members": [{"user_id": "skill_member_two", "role": "member"}]},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="PUT",
                path="/api/skills/{skill_id}/members/skill_member_one/role",
                body={"role": "admin"},
                expect_status=200,
                expect={"success": True, "data": {"user_id": "skill_member_one", "role": "admin"}},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{skill_id}/members",
                expect_status=200,
                expect={"success": True, "count": 2},
            ),
            FlowStep(
                method="DELETE",
                path="/api/skills/{skill_id}/members/skill_member_two",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/with-mcps",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/resources",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/market/sync",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/market/list",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/market/tree",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/market/local",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/market/search",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                body={"query": "brainstorming", "page": 1, "page_size": 20},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/market/sync-status",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/upload",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                form={
                    "file_paths": (
                        '["uploaded-singlebox-skill/SKILL.md",'
                        '"uploaded-singlebox-skill/README.md"]'
                    )
                },
                files=[
                    FlowFile(
                        field="files",
                        filename="uploaded-singlebox-skill/SKILL.md",
                        content=(
                            "name: uploaded-singlebox-skill\n"
                            "description: Uploaded by singlebox coverage E2E\n"
                            "category: crud\n"
                            "tags:\n"
                            "  - singlebox\n"
                            "  - upload\n"
                        ),
                        content_type="text/markdown",
                    ),
                    FlowFile(
                        field="files",
                        filename="uploaded-singlebox-skill/README.md",
                        content="# Uploaded Singlebox Skill\n",
                        content_type="text/markdown",
                    ),
                ],
                expect={
                    "success": True,
                    "data": {"name": "uploaded-singlebox-skill"},
                },
                extract={"uploaded_skill_id": "data.id"},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{uploaded_skill_id}",
                expect_status=200,
                expect={"success": True, "data": {"name": "uploaded-singlebox-skill"}},
                extract={"uploaded_git_path": "data.git_path"},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{uploaded_skill_id}/readme",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{uploaded_skill_id}/risk-tags",
                body={
                    "user_id": "{user_id}",
                    "risk_tags": [
                        {"code": "singlebox", "level": "low", "source": "coverage"}
                    ],
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{uploaded_skill_id}/mcp-dependencies",
                body={
                    "user_id": "{user_id}",
                    "mcp_dependencies": [
                        {
                            "code": "mcp.singlebox.acceptance",
                            "name": "mcp.singlebox.acceptance",
                            "server_code": "mcp.singlebox.acceptance",
                            "required": False,
                        }
                    ],
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skill/permission",
                query={"skill_id": "{uploaded_skill_id}"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skill/permission/apply",
                body={"skill_id": "{uploaded_skill_id}", "reason": "singlebox coverage with mcp"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/user/my-skills",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{uploaded_skill_id}/activate",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                body={"source_path": "{uploaded_git_path}", "relative_path": "{uploaded_git_path}"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/market/activate-batch",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                body={"skill_paths": ["{uploaded_git_path}"]},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{uploaded_skill_id}/publish",
                body={"user_id": "{user_id}"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{uploaded_skill_id}/publish/status",
                expect_status=200,
                expect={"success": True, "data": {"local_status": "PUBLISHED"}},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{uploaded_skill_id}/versions",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{uploaded_skill_id}/versions/1.0.0/download-url",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{uploaded_skill_id}/file-structure",
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/{uploaded_skill_id}/file-content",
                query={"filePath": "SKILL.md"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/{uploaded_skill_id}/publish/upgrade",
                body={"user_id": "{user_id}"},
                expect_status=200,
                expect={"success": True, "data": {"new_status": "DEVELOPING"}},
                extract={"upgraded_skill_id": "data.new_skill_id"},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/active/list",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/skillset/active",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/deactivate-all",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/{skill_set_id}",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}"},
                expect_status=200,
                expect={"success": True, "data": {"name": "API Lifecycle SkillSet"}},
            ),
            FlowStep(
                method="PUT",
                path="/api/skillsets/{skill_set_id}",
                body={
                    "description": "updated by singlebox API lifecycle",
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={
                    "success": True,
                    "data": {"description": "updated by singlebox API lifecycle"},
                },
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets/{skill_set_id}/skills",
                body={
                    "skill_ids": ["{skill_id}"],
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skill/set/permission",
                query={"skill_set_id": "{skill_set_id}"},
                expect_status=200,
                expect={"success": True, "authorized": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skill/set/permission/apply",
                body={"skill_set_id": "{skill_set_id}", "reason": "singlebox coverage"},
                expect_status=200,
                expect={"success": True, "all_authorized": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skill/bot/permission",
                query={"bot_id": "{bot_id}"},
                expect_status=200,
                expect={"success": True, "authorized": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skill/bot/permission/apply",
                body={"bot_id": "{bot_id}", "reason": "singlebox coverage"},
                expect_status=200,
                expect={"success": True, "all_authorized": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/{skill_set_id}/skills",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}"},
                expect_status=200,
                expect={"success": True, "count": 1},
            ),
            FlowStep(
                method="DELETE",
                path="/api/skillsets/{skill_set_id}/skills/{skill_id}",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/{skill_set_id}/skills",
                query={"user_id": "{user_id}", "bot_id": "{bot_id}"},
                expect_status=200,
                expect={"success": True, "count": 0},
            ),
            FlowStep(
                method="DELETE",
                path="/api/skills/{upgraded_skill_id}",
                query={"user_id": "{user_id}", "entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="DELETE",
                path="/api/skills/{uploaded_skill_id}",
                query={"user_id": "{user_id}", "entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
        ],
    ),
    # Flow 5: exercise the user-facing skill-set operations beyond the happy
    # CRUD path. A user can create a bot, build a skill set, attach a skill,
    # activate/sync/switch/deactivate it, and manage an MCP binding on that set.
    # The final delete of an already-removed MCP is the meaningful negative
    # path: it is a real user action ("remove again") with a stable 404 result.
    FlowCase(
        name="skill_center-skillset-activation-and-mcp-lifecycle",
        covers=["skill_center"],
        live_only=True,
        steps=[
            FlowStep(
                method="POST",
                path="/api/bots",
                body={"bot_name": "SkillSet Activation Bot", "bot_type": "personal"},
                expect_status=200,
                extract={"user_id": "data.bot.owner_id", "bot_id": "data.bot.bot_id"},
            ),
            FlowStep(
                method="GET",
                path="/api/bots/{bot_id}/status",
                expect_status=200,
                expect={"success": True, "data": {"is_ready": True}},
                poll_timeout_sec=180,
                poll_interval_sec=2,
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets/default/ensure",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
                extract={"default_skill_set_id": "data.id"},
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets",
                body={
                    "name": "Activation MCP SkillSet",
                    "description": "singlebox activation and MCP lifecycle",
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={"success": True, "data": {"name": "Activation MCP SkillSet"}},
                extract={"skill_set_id": "data.id"},
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets",
                body={
                    "name": "Activation MCP SkillSet",
                    "description": "duplicate name should be rejected",
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=400,
            ),
            FlowStep(
                method="POST",
                path="/api/skills",
                body={
                    "name": "activation-brainstorming",
                    "description": "activation flow git skill",
                    "git_path": "git://infra/common/brainstorming",
                    "category": "activation",
                    "tags": ["singlebox", "activation"],
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={"success": True, "data": {"name": "activation-brainstorming"}},
                extract={"skill_id": "data.id"},
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets/{skill_set_id}/skills",
                body={
                    "skill_ids": ["{skill_id}"],
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets/{skill_set_id}/skills",
                body={
                    "skill_ids": ["missing-skill-id"],
                    "user_id": "{user_id}",
                    "bot_id": "{bot_id}",
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/skillset/sync",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                body={"skill_set_id": "{skill_set_id}"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/skillset/activate",
                body={
                    "skill_set_id": "{skill_set_id}",
                    "entity_id": "{user_id}",
                    "bot_id": "{bot_id}",
                    "engine_type": "openclaw",
                },
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/skillset/switch",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                body={"skill_set_id": "{skill_set_id}"},
                expect_status=200,
                # singlebox/openclaw MCP filter-servers may return a device-side
                # failure while the skill-set switch itself has been attempted.
                # Keep this as a real user operation, but do not make current
                # MCP-scope infrastructure readiness the gate for this flow.
            ),
            FlowStep(
                method="GET",
                path="/api/skills/skillset/current",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skills/skillset/active",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/{default_skill_set_id}/mcps",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skillsets/{skill_set_id}/mcps",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                body={"server_code": "mcp.singlebox.acceptance", "user_id": "{user_id}"},
                # Known current singlebox gap: openclaw /api/mcp/filter-servers
                # returns 500, and add-MCP currently lets that surface as a 500.
                # Keep the user operation in coverage so this defect stays
                # visible instead of silently dropping the path.
                expect_status=500,
            ),
            FlowStep(
                method="GET",
                path="/api/skillsets/{skill_set_id}/mcps",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=200,
                expect={"success": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skills/skillset/deactivate",
                body={
                    "skill_set_id": "{skill_set_id}",
                    "entity_id": "{user_id}",
                    "bot_id": "{bot_id}",
                    "engine_type": "openclaw",
                },
                expect_status=200,
                # Device-side MCP scope refresh can fail in current singlebox;
                # status 200 keeps the endpoint behavior covered without
                # hiding that infrastructure gap as a test assertion.
            ),
            FlowStep(
                method="DELETE",
                path="/api/skillsets/{skill_set_id}/mcps/mcp.singlebox.missing",
                query={"entity_id": "{user_id}", "bot_id": "{bot_id}", "engine_type": "openclaw"},
                expect_status=404,
            ),
            FlowStep(
                method="POST",
                path="/api/skills/skillset/deactivate",
                body={
                    "skill_set_id": "{default_skill_set_id}",
                    "entity_id": "{user_id}",
                    "bot_id": "{bot_id}",
                    "engine_type": "openclaw",
                },
                expect_status=200,
                expect={"success": False},
            ),
        ],
    ),
    # Flow 6: manually-triggered scan/auth guardrails. These are operational
    # user paths, not the deferred GitSync lifecycle专项: users can inspect scan
    # status, trigger a scan with bad input and get a clear failure, and ask
    # permission APIs about missing skills. It intentionally runs after the
    # longer SkillSet lifecycle flow so scan-service side effects cannot affect
    # the primary user journey.
    FlowCase(
        name="skill_center-scan-and-auth-guardrails",
        covers=["skill_center"],
        live_only=True,
        steps=[
            FlowStep(
                method="GET",
                path="/api/skill-scan/status",
                expect_status=200,
                expect={"success": True, "service_started": True},
            ),
            FlowStep(
                method="POST",
                path="/api/skill-scan/scan/center",
                body={"skill_uuids": [], "env": "dev"},
                expect_status=400,
            ),
            FlowStep(
                method="POST",
                path="/api/skill-scan/scan/skill",
                body={"skill_path": "/tmp/singlebox-missing-skill/SKILL.md"},
                expect_status=500,
            ),
            FlowStep(
                method="GET",
                path="/api/skill/permission",
                query={"skill_id": "missing-skill-id"},
                expect_status=404,
            ),
        ],
    ),
]
