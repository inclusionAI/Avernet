"""HTTP contracts for explicitly selected Cron containers."""

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.cron.schemas import CronTargetRequest
from tests.community.endpoints.test_cron_router import (
    _seed_bot_with_owner,
    _seed_bot_with_collaborator,
)
from tests.community.framework import (
    CaseInput,
    ExpectSuccess,
    ExpectError,
    endpoint_test,
)


@pytest.mark.parametrize(
    "target",
    [
        {"runtime_stage": "production"},
        {"runtime_stage": "online", "device_id": 123},
        {"runtime_stage": "online", "create_by_id": ""},
        {"runtime_stage": "online", "device_id": "a", "device_uuid": "b"},
        {"device_id": "a"},
        {"create_by_id": "caller"},
    ],
)
def test_target_schema_rejects_invalid_fields(target):
    with pytest.raises(ValidationError):
        CronTargetRequest(**target)


def test_target_schema_normalizes_matching_aliases():
    target = CronTargetRequest(runtime_stage="online", device_id="a", device_uuid="a")
    assert target.device_id == "a"
    assert CronTargetRequest(runtime_stage="draft", device_uuid="a").device_id == "a"
    assert CronTargetRequest().device_id is None


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="explicit_draft_target",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
            "name": "Daily",
            "schedule": "0 9 * * *",
            "command": "Summarize",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def create_explicit_draft():
    """The actual DI service resolves the existing local binding."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="invalid_target_aliases",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "runtime_stage": "online",
            "device_id": "a",
            "device_uuid": "b",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 400}),
)
def create_invalid_aliases():
    """Conflicting instance aliases are rejected before domain forwarding."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="invalid_target_stage",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={"bot_id": "bot_test", "runtime_stage": "prod"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 400}),
)
def create_invalid_stage():
    """Runtime stage is a validated field, not an engine body extension."""


@endpoint_test(
    method="POST",
    path="/api/cron",
    scenario="admin_delegates_creation",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
            "name": "Daily",
            "schedule": "0 9 * * *",
            "command": "Summarize",
        },
    ),
    seed=_seed_bot_with_collaborator,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def create_admin_delegation():
    """Existing ADMIN permission grants delegation without replacing the operator."""


@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="targeted_list",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def list_targeted_crons():
    """Target fields reach the domain service through the real router."""


@endpoint_test(
    method="GET",
    path="/api/cron",
    scenario="invalid_targeted_list",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "device_id": "a",
            "device_uuid": "b",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectError(status=200, json_contains={"success": False, "error_code": 400}),
)
def list_invalid_alias():
    """Listing retains business-error status for target validation."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}",
    scenario="targeted_detail",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        path_params={"task_id": "task_001"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def detail_targeted_cron():
    """Detail accepts the same creator routing fields."""


@endpoint_test(
    method="PUT",
    path="/api/cron/{task_id}",
    scenario="targeted_update",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        path_params={"task_id": "task_001"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
        },
        json_body={"name": "Updated"},
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def update_targeted_cron():
    """Update forwards job content independently from target metadata."""


@endpoint_test(
    method="DELETE",
    path="/api/cron/{task_id}",
    scenario="targeted_delete",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        path_params={"task_id": "task_001"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def delete_targeted_cron():
    """Deletion uses the selected creator's existing binding."""


@endpoint_test(
    method="POST",
    path="/api/cron/{task_id}/run",
    scenario="targeted_run",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        path_params={"task_id": "task_001"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
            "force": True,
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def run_targeted_cron():
    """Run retains force while selecting one target."""


@endpoint_test(
    method="GET",
    path="/api/cron/{task_id}/runs",
    scenario="targeted_runs",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        path_params={"task_id": "task_001"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def runs_targeted_cron():
    """History reads the same target as creation."""


@endpoint_test(
    method="GET",
    path="/api/cron/running",
    scenario="targeted_running",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
        },
    ),
    seed=_seed_bot_with_owner,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def running_targeted_cron():
    """Running requests select one existing target."""


def _seed_with_adapter_failure(world):
    from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterTransport

    _seed_bot_with_owner(world)
    world.get(DeviceAdapterTransport).set_response(
        "invoke",
        {
            "success": False,
            "message": "adapter unavailable",
            "error_code": 503,
            "data": None,
        },
    )


@endpoint_test(
    method="GET",
    path="/api/cron/running",
    scenario="targeted_running_failure",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={
            "bot_id": "bot_test",
            "runtime_stage": "draft",
            "create_by_id": "u_owner",
        },
    ),
    seed=_seed_with_adapter_failure,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "message": "adapter unavailable",
            "error_code": 503,
        },
    ),
)
def running_failure_preserves_result():
    """An adapter failure must not be presented as an empty successful list."""
