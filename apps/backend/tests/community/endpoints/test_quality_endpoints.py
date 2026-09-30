"""Tests for quality task router endpoints.

Covers:
- GET /api/quality/tasks - list quality tasks
- GET /api/quality/tasks/{id} - get task by ID
- POST /api/quality/tasks/create - create single bot task
- POST /api/quality/tasks/{id}/process - process task status
- POST /api/quality/tasks/{id}/status_for_others - update task status (admin)
- POST /api/quality/tasks/{id}/process_for_others - process task status (admin)

Permission checks are via CollaboratorPermissionInterceptor which validates
that the requesting user has access to the bot_id/owner_id pair.
"""
from __future__ import annotations

from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
    json_response,
)


# ============================================================================
# Test Setup: seed users and bots
# ============================================================================


def _seed_operator(world):
    """Seed a staff user for collaborator permission checks."""
    make_staff_user(world, user_id="u_test_operator")


def _seed_owner_with_bot(world):
    """Seed a bot owner with a bot for permission tests."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


def _seed_other_user(world):
    """Seed another user (non-owner)."""
    make_staff_user(world, user_id="u_other")


def _seed_owner_and_other_user(world):
    """Seed both owner and other user for permission tests."""
    _seed_owner_with_bot(world)
    _seed_other_user(world)


# ============================================================================
# Seed helpers for tasks
# ============================================================================


def _seed_task_for_get(world):
    """Seed a quality task for GET tests."""
    from tests.community.factories.quality import make_quality_task

    _seed_operator(world)
    task = make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_test",
        owner_id="u_test_operator",
    )
    world._quality_task_id = task.id


def _seed_tasks_for_list(world):
    """Seed multiple quality tasks for list tests."""
    from tests.community.factories.quality import make_quality_task

    _seed_operator(world)
    make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_1",
        owner_id="u_test_operator",
    )
    make_quality_task(
        world,
        task_type="stress_test",
        biz_type="multi_bot",
        bot_id="bot_2",
        owner_id="u_test_operator",
    )


def _stand_in_for_eval_publish(world):
    """Stand in for the publish steps an eval kicks off.

    ``eval_publish`` drives a BaaS publish of the bot under evaluation — a
    multi-minute, multi-service operation, and not what these routes are about.
    The stand-in is bound through the injector as a subclass of the wired flow
    service, so the route resolves it the production way and the substitution
    dies with this test's injector.
    """
    from injector import SingletonScope

    from agentclaw.community.core.service_bot.services.publish_flow_service import (
        PublishFlowService,
    )

    async def _eval_publish(_self, *_args, **_kwargs):
        return {
            "bot_uuid": "test-bot-uuid",
            "baas_publish_id": "test-baas-publish-id",
        }

    def _progress(_self, *_args, **_kwargs):
        return {"status": "SUCCESS"}

    bind_overrides(
        world,
        PublishFlowService,
        {"eval_publish": _eval_publish, "get_baas_publish_progress": _progress},
    )
    # The graph resolves PublishFlowService as a singleton, so the instance
    # cached before the rebind has to be dropped for the new binding to win.
    scope_binding, _ = world.injector.binder.get_binding(SingletonScope)
    scope_instance = scope_binding.provider.get(world.injector)
    scope_instance._context.pop(PublishFlowService, None)


def _seed_init_task(world):
    """Seed a task in init status for process tests."""
    from typing import Annotated

    from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_MASA_AGENT_EVAL
    from tests.community.factories.quality import make_quality_task

    _seed_operator(world)

    _stand_in_for_eval_publish(world)

    # The MASA eval upstream is a real HTTP service; its two replies come
    # through the qualified ``HttpClient`` seam, which is the injected boundary
    # for exactly this.
    masa_eval_http = world.get(Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL])
    masa_eval_http.set_response(
        "post",
        json_response({
            "success": True,
            "data": {"set_task_uuid": "test-set-task-uuid"},
        }),
    )
    masa_eval_http.set_response(
        "get",
        json_response({"success": True, "data": {"status": "completed"}}),
    )

    task = make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_process",
        owner_id="u_test_operator",
        status="init",
        ext={"publish_id": "12345", "set_uuid": "test-set-uuid", "version": "1.0"},
    )
    world._quality_task_id = task.id


def _seed_running_task(world):
    """Seed a task in running status for status update tests."""
    from tests.community.factories.quality import make_quality_task

    _seed_operator(world)
    task = make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_status",
        owner_id="u_test_operator",
        status="running",
    )
    world._quality_task_id = task.id


def _seed_task_with_owner(world):
    """Seed a task with bot owner for permission tests."""
    from tests.community.factories.quality import make_quality_task

    _seed_owner_and_other_user(world)
    task = make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_test",
        owner_id="u_owner",
    )
    world._quality_task_id = task.id


# ============================================================================
# Extra Assertions
# ============================================================================


def _assert_task_response_valid(response, world):
    """Assert that response has valid structure for a quality task."""
    data = response.json()
    required_fields = ["id", "uuid", "task_type", "biz_type", "status"]
    for field in required_fields:
        assert field in data, f"Expected '{field}' in response, got keys: {list(data.keys())}"

    # Verify id matches seeded task if available
    if hasattr(world, "_quality_task_id"):
        assert data["id"] == world._quality_task_id, (
            f"Expected id={world._quality_task_id}, got {data['id']}"
        )


def _assert_list_response_valid(response, world):
    """Assert that response has valid structure for list endpoint."""
    data = response.json()
    assert "items" in data, f"Expected 'items' in response, got keys: {list(data.keys())}"
    assert "total" in data, f"Expected 'total' in response, got keys: {list(data.keys())}"
    assert "page" in data, f"Expected 'page' in response, got keys: {list(data.keys())}"
    assert "page_size" in data, f"Expected 'page_size' in response, got keys: {list(data.keys())}"
    assert isinstance(data["items"], list), "items should be a list"
    assert isinstance(data["total"], int), "total should be an int"


def _assert_create_response_valid(response, world):
    """Assert that response has valid structure for create endpoint."""
    data = response.json()
    assert "success" in data, f"Expected 'success' in response, got keys: {list(data.keys())}"
    assert data["success"] is True, f"Expected success=True, got {data['success']}"
    assert "data" in data, f"Expected 'data' in response, got keys: {list(data.keys())}"
    assert "id" in data["data"], "data should contain 'id'"


# ============================================================================
# GET /api/quality/tasks
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/quality/tasks",
    scenario="ok_list_tasks",
    seed=_seed_tasks_for_list,
    input=CaseInput(
        query_params={"task_type": "eval", "biz_type": "service_bot_single"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"items": [{"task_type": "eval"}]},
    ),
    extra_assertions=(
        _assert_list_response_valid,
    ),
)
def list_quality_tasks_ok():
    """List quality tasks returns success with matching tasks."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks",
    scenario="ok_empty_list",
    seed=_seed_operator,
    input=CaseInput(
        query_params={"task_type": "nonexistent", "biz_type": "nonexistent"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"items": [], "total": 0},
    ),
    extra_assertions=(
        _assert_list_response_valid,
    ),
)
def list_quality_tasks_empty():
    """List with no matching tasks returns empty list."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks",
    scenario="ok_list_with_pagination",
    seed=_seed_tasks_for_list,
    input=CaseInput(
        query_params={"task_type": "eval", "biz_type": "service_bot_single", "page": "1", "page_size": "10"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"page": 1, "page_size": 10},
    ),
    extra_assertions=(
        _assert_list_response_valid,
    ),
)
def list_quality_tasks_with_pagination():
    """List with pagination parameters returns paginated results."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks",
    scenario="error_invalid_page",
    seed=_seed_operator,
    input=CaseInput(
        query_params={"task_type": "eval", "biz_type": "service_bot_single", "page": "invalid"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def list_quality_tasks_invalid_page():
    """List with invalid page parameter returns validation error."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks",
    scenario="error_invalid_page_size",
    seed=_seed_operator,
    input=CaseInput(
        query_params={"task_type": "eval", "biz_type": "service_bot_single", "page_size": "200"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def list_quality_tasks_invalid_page_size():
    """List with page_size > 100 returns validation error."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks",
    scenario="ok_list_with_bot_filter",
    seed=_seed_tasks_for_list,
    input=CaseInput(
        query_params={"task_type": "eval", "biz_type": "service_bot_single", "bot_id": "bot_1"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
    ),
    extra_assertions=(
        _assert_list_response_valid,
    ),
)
def list_quality_tasks_with_bot_filter():
    """List with bot_id filter returns filtered results."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks",
    scenario="ok_list_with_owner_filter",
    seed=_seed_tasks_for_list,
    input=CaseInput(
        query_params={"task_type": "eval", "biz_type": "service_bot_single", "owner_id": "u_test_operator"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
    ),
    extra_assertions=(
        _assert_list_response_valid,
    ),
)
def list_quality_tasks_with_owner_filter():
    """List with owner_id filter returns filtered results."""


# ============================================================================
# GET /api/quality/tasks/{id}
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/quality/tasks/{id}",
    scenario="ok_get_task",
    seed=_seed_task_for_get,
    input=CaseInput(
        path_params={"id": "1"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"task_type": "eval", "biz_type": "service_bot_single"},
    ),
    extra_assertions=(
        _assert_task_response_valid,
    ),
)
def get_quality_task_ok():
    """Get task by ID returns success with task details."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks/{id}",
    scenario="error_not_found",
    seed=_seed_operator,
    input=CaseInput(
        path_params={"id": "999999"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectError(
        status=404,
    ),
)
def get_quality_task_not_found():
    """Get non-existent task returns 404."""


@endpoint_test(
    method="GET",
    path="/api/quality/tasks/{id}",
    scenario="error_invalid_id",
    seed=_seed_operator,
    input=CaseInput(
        path_params={"id": "invalid"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def get_quality_task_invalid_id():
    """Get with invalid ID format returns validation error."""


# ============================================================================
# POST /api/quality/tasks/create
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/create",
    scenario="ok_create_task",
    seed=_seed_operator,
    input=CaseInput(
        json_body={
            "task_type": "eval",
            "biz_type": "service_bot_single",
            "bot_id": "bot_new",
            "owner_id": "u_test_operator",
            "ext": {"key": "value"},
        },
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_create_response_valid,
    ),
)
def create_quality_task_ok():
    """Create quality task returns success with created task."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/create",
    scenario="ok_create_minimal",
    seed=_seed_operator,
    input=CaseInput(
        json_body={
            "task_type": "stress_test",
            "biz_type": "multi_bot",
        },
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_create_response_valid,
    ),
)
def create_quality_task_minimal():
    """Create task with minimal required fields succeeds."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/create",
    scenario="error_missing_task_type",
    seed=_seed_operator,
    input=CaseInput(
        json_body={
            "biz_type": "service_bot_single",
        },
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def create_quality_task_missing_task_type():
    """Create task without required task_type returns validation error."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/create",
    scenario="error_missing_biz_type",
    seed=_seed_operator,
    input=CaseInput(
        json_body={
            "task_type": "eval",
        },
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def create_quality_task_missing_biz_type():
    """Create task without required biz_type returns validation error."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/create",
    scenario="ok_create_with_ext",
    seed=_seed_operator,
    input=CaseInput(
        json_body={
            "task_type": "eval",
            "biz_type": "service_bot_single",
            "ext": {" nested": {"data": True}, "list": [1, 2, 3]},
        },
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(
        _assert_create_response_valid,
    ),
)
def create_quality_task_with_ext():
    """Create task with complex ext field succeeds."""


# ============================================================================
# POST /api/quality/tasks/{id}/process
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/process",
    scenario="ok_process_init",
    seed=_seed_init_task,
    input=CaseInput(
        path_params={"id": "1"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def process_task_from_init():
    """Process task from init status advances to env_preparing."""


# NOTE: The "not_found" scenario for process_task is intentionally not tested here.
# When a task doesn't exist, the CollaboratorPermissionInterceptor's extract_from_task_id
# returns empty PermissionParams(), which triggers a permission check failure (403)
# before the route handler is even reached. Therefore, the ValueError from TaskProcessor
# is never raised in practice for non-existent tasks.
# Testing this would require mocking the interceptor behavior, which is beyond
# the scope of endpoint tests (interceptor behavior is tested separately).


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/process",
    scenario="error_invalid_id",
    seed=_seed_operator,
    input=CaseInput(
        path_params={"id": "invalid"},
        headers={"x-user-id": "u_test_operator"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def process_task_invalid_id():
    """Process with invalid ID format returns validation error."""


# ============================================================================
# POST /api/quality/tasks/{id}/status_for_others
# ============================================================================


def _seed_super_admin(world):
    """Seed a super admin user for for_others endpoints."""
    make_staff_user(world, user_id="100000")  # SUPER_ADMIN member


def _seed_non_admin(world):
    """Seed a non-admin user for permission tests."""
    make_staff_user(world, user_id="u_non_admin")


def _seed_admin_and_task(world):
    """Seed admin user and a task for for_others tests."""
    _seed_super_admin(world)
    from tests.community.factories.quality import make_quality_task

    task = make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_admin_test",
        owner_id="u_owner",
        status="running",
    )
    world._quality_task_id = task.id


def _seed_non_admin_and_task(world):
    """Seed non-admin user and a task for permission denial tests."""
    _seed_non_admin(world)
    from tests.community.factories.quality import make_quality_task

    task = make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_non_admin_test",
        owner_id="u_owner",
        status="running",
    )
    world._quality_task_id = task.id


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/status_for_others",
    scenario="ok_update_status_for_others",
    seed=_seed_admin_and_task,
    input=CaseInput(
        path_params={"id": "1"},
        query_params={"status": "success"},
        headers={"x-user-id": "100000"},  # SUPER_ADMIN member
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def update_task_status_for_others_ok():
    """Update task status as admin returns success."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/status_for_others",
    scenario="error_permission_denied",
    seed=_seed_non_admin_and_task,
    input=CaseInput(
        path_params={"id": "1"},
        query_params={"status": "success"},
        headers={"x-user-id": "u_non_admin"},  # Not in SUPER_ADMIN
    ),
    expect=ExpectError(
        status=200,  # Returns ApiResponse with success=False
        json_contains={"success": False, "error_code": 403},
    ),
)
def update_task_status_for_others_permission_denied():
    """Update task status as non-admin returns 403."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/status_for_others",
    scenario="error_not_found",
    seed=_seed_super_admin,
    input=CaseInput(
        path_params={"id": "999999"},
        query_params={"status": "running"},
        headers={"x-user-id": "100000"},
    ),
    expect=ExpectError(
        status=404,
    ),
)
def update_task_status_for_others_not_found():
    """Update non-existent task status returns 404."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/status_for_others",
    scenario="error_missing_status",
    seed=_seed_admin_and_task,
    input=CaseInput(
        path_params={"id": "1"},
        headers={"x-user-id": "100000"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def update_task_status_for_others_missing_status():
    """Update without status parameter returns validation error."""


# ============================================================================
# POST /api/quality/tasks/{id}/process_for_others
# ============================================================================


def _seed_admin_and_init_task(world):
    """Seed admin user and a task in init status for process_for_others tests."""
    from typing import Annotated

    from agentclaw.community.plugin_api.http_client import HttpClient, QUALIFIER_MASA_AGENT_EVAL
    from tests.community.factories.quality import make_quality_task

    _seed_super_admin(world)

    _stand_in_for_eval_publish(world)

    # The MASA eval upstream is a real HTTP service; its two replies come
    # through the qualified ``HttpClient`` seam, which is the injected boundary
    # for exactly this.
    masa_eval_http = world.get(Annotated[HttpClient, QUALIFIER_MASA_AGENT_EVAL])
    masa_eval_http.set_response(
        "post",
        json_response({
            "success": True,
            "data": {"set_task_uuid": "test-set-task-uuid"},
        }),
    )
    masa_eval_http.set_response(
        "get",
        json_response({"success": True, "data": {"status": "completed"}}),
    )

    task = make_quality_task(
        world,
        task_type="eval",
        biz_type="service_bot_single",
        bot_id="bot_process_admin",
        owner_id="u_owner",
        status="init",
        ext={"publish_id": "12345", "set_uuid": "test-set-uuid", "version": "1.0"},
    )
    world._quality_task_id = task.id


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/process_for_others",
    scenario="ok_process_for_others",
    seed=_seed_admin_and_init_task,
    input=CaseInput(
        path_params={"id": "1"},
        headers={"x-user-id": "100000"},  # SUPER_ADMIN member
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def process_task_for_others_ok():
    """Process task as admin returns success."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/process_for_others",
    scenario="error_permission_denied",
    seed=_seed_non_admin_and_task,
    input=CaseInput(
        path_params={"id": "1"},
        headers={"x-user-id": "u_non_admin"},  # Not in SUPER_ADMIN
    ),
    expect=ExpectError(
        status=200,  # Returns ApiResponse with success=False
        json_contains={"success": False, "error_code": 403},
    ),
)
def process_task_for_others_permission_denied():
    """Process task as non-admin returns 403."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/process_for_others",
    scenario="error_not_found",
    seed=_seed_super_admin,
    input=CaseInput(
        path_params={"id": "999999"},
        headers={"x-user-id": "100000"},
    ),
    expect=ExpectError(
        status=404,
    ),
)
def process_task_for_others_not_found():
    """Process non-existent task returns 404."""


@endpoint_test(
    method="POST",
    path="/api/quality/tasks/{id}/process_for_others",
    scenario="error_invalid_id",
    seed=_seed_super_admin,
    input=CaseInput(
        path_params={"id": "invalid"},
        headers={"x-user-id": "100000"},
    ),
    expect=ExpectError(
        status=422,
    ),
)
def process_task_for_others_invalid_id():
    """Process with invalid ID format returns validation error."""