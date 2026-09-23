"""Cron Guard M2M API must fail closed and preserve the existing relay semantics."""

import pytest
from fastapi import HTTPException

from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.adapters.http.cron.internal_auth import verify_cron_guard_token
from agentclaw.community.adapters.http.cron.internal_router import (
    DisableCronRequest,
    _fingerprint,
    disable_target_cron,
    get_target_runs,
    list_target_crons,
)
from agentclaw.community.di.config import CronGuardInternalToken
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


TASK = {"id": "19348c71-e6d0-4398-a9a6-f4855b6933e5", "name": "daily-check",
        "enabled": True, "schedule": {"kind": "every", "everyMs": 300000},
        "payload": {"message": "check"}, "runtime_stage": "online"}


class FakeRelay:
    def __init__(self, task=None, failed_targets=None):
        self.task = TASK if task is None else task
        self.failed_targets = failed_targets or []
        self.updates = []

    async def list_all_crons(self, **kwargs):
        assert kwargs["runtime_stage"] == "online"
        return {"success": True, "data": [self.task],
                "failed_targets": self.failed_targets}

    async def update_cron(self, **kwargs):
        self.updates.append(kwargs)
        return {"success": True, "data": {"id": self.task["id"], "enabled": False}}

    async def get_cron_runs(self, **kwargs):
        return {"success": True, "data": {"input": "private prompt",
                "runs": [{"job_id": TASK["id"], "status": "error",
                          "started_at_ms": 123, "error": "timeout"}]}}


class EndpointRelay(FakeRelay):
    """Small relay boundary for the generic HTTP endpoint coverage cases."""


def _bind_endpoint_dependencies(world, *, failed_targets=None):
    relay = EndpointRelay(failed_targets=failed_targets)
    world.injector.binder.bind(CronRelayServiceProtocol, to=relay, scope=None)
    world.injector.binder.bind(
        CronGuardInternalToken, to=CronGuardInternalToken("cron-guard-test-token"),
        scope=None,
    )


def _seed_endpoint_happy(world):
    _bind_endpoint_dependencies(world)


def _seed_endpoint_list_failure(world):
    _bind_endpoint_dependencies(world, failed_targets=[{"reason": "offline"}])


def _disable_body(expected_fingerprint):
    return {
        "target_user_id": "owner",
        "target_bot_id": "bot",
        "task_id": TASK["id"],
        "expected_fingerprint": expected_fingerprint,
        "improvement_id": 123,
        "operator": "admin",
        "reason": "approved cron guard",
    }


_AUTH = {"Authorization": "Bearer cron-guard-test-token"}
_TARGET = {"target_user_id": "owner", "target_bot_id": "bot"}


@endpoint_test(
    method="GET",
    path="/api/internal/cron-guard",
    scenario="ok",
    input=CaseInput(headers=_AUTH, query_params=_TARGET),
    seed=_seed_endpoint_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def list_cron_guard_targets_ok():
    """List configured cron tasks through the authenticated HTTP route."""


@endpoint_test(
    method="GET",
    path="/api/internal/cron-guard",
    scenario="incomplete_target_list",
    input=CaseInput(headers=_AUTH, query_params=_TARGET),
    seed=_seed_endpoint_list_failure,
    expect=ExpectError(status=503),
)
def list_cron_guard_targets_incomplete():
    """An incomplete relay fan-out must be surfaced as service unavailable."""


@endpoint_test(
    method="GET",
    path="/api/internal/cron-guard/{task_id}/runs",
    scenario="ok",
    input=CaseInput(
        headers=_AUTH,
        query_params=_TARGET,
        path_params={"task_id": TASK["id"]},
    ),
    seed=_seed_endpoint_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def get_cron_guard_runs_ok():
    """Read sanitized execution history for a listed cron task."""


@endpoint_test(
    method="GET",
    path="/api/internal/cron-guard/{task_id}/runs",
    scenario="task_not_found",
    input=CaseInput(
        headers=_AUTH,
        query_params=_TARGET,
        path_params={"task_id": "missing-task"},
    ),
    seed=_seed_endpoint_happy,
    expect=ExpectError(status=404),
)
def get_cron_guard_runs_missing_task():
    """Reject run-history requests for tasks outside the target's current list."""


@endpoint_test(
    method="POST",
    path="/api/internal/cron-guard/disable",
    scenario="ok",
    input=CaseInput(
        headers=_AUTH,
        json_body=_disable_body(_fingerprint(TASK)),
    ),
    seed=_seed_endpoint_happy,
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
)
def disable_cron_guard_task_ok():
    """Disable a task only after its current configuration fingerprint matches."""


@endpoint_test(
    method="POST",
    path="/api/internal/cron-guard/disable",
    scenario="configuration_changed",
    input=CaseInput(
        headers=_AUTH,
        json_body=_disable_body("0" * 64),
    ),
    seed=_seed_endpoint_happy,
    expect=ExpectError(status=409),
)
def disable_cron_guard_task_changed():
    """Reject disable when the cron configuration changed after review."""


@pytest.mark.asyncio
async def test_auth_requires_configured_matching_token():
    with pytest.raises(HTTPException) as missing:
        await verify_cron_guard_token("Bearer guessed", CronGuardInternalToken())
    assert missing.value.status_code == 401
    await verify_cron_guard_token("Bearer expected", CronGuardInternalToken("expected"))


@pytest.mark.asyncio
async def test_list_normalizes_openclaw_id_without_exposing_prompt():
    result = await list_target_crons("owner", "bot", FakeRelay())
    assert result["data"][0]["task_id"] == TASK["id"]
    assert "payload" not in result["data"][0]


@pytest.mark.asyncio
async def test_disable_requires_matching_configuration_and_online_scope():
    relay = FakeRelay()
    request = DisableCronRequest(
        target_user_id="owner", target_bot_id="bot", task_id=TASK["id"],
        expected_fingerprint=_fingerprint(TASK), improvement_id=123,
        operator="admin", reason="approved cron guard",
    )
    result = await disable_target_cron(request, relay)
    assert result["success"] is True
    assert relay.updates[0]["body"] == {"enabled": False}
    assert relay.updates[0]["runtime_stage"] == "online"

    request.expected_fingerprint = "0" * 64
    with pytest.raises(HTTPException) as changed:
        await disable_target_cron(request, relay)
    assert changed.value.status_code == 409
    assert len(relay.updates) == 1


@pytest.mark.asyncio
async def test_incomplete_list_and_disabled_task_do_not_mutate():
    relay = FakeRelay(failed_targets=[{"reason": "offline"}])
    with pytest.raises(HTTPException) as incomplete:
        await list_target_crons("owner", "bot", relay)
    assert incomplete.value.status_code == 503

    disabled = dict(TASK, enabled=False)
    relay = FakeRelay(task=disabled)
    request = DisableCronRequest(
        target_user_id="owner", target_bot_id="bot", task_id=TASK["id"],
        expected_fingerprint=_fingerprint(disabled), improvement_id=123,
        operator="admin", reason="approved cron guard",
    )
    assert (await disable_target_cron(request, relay))["already_disabled"] is True
    assert relay.updates == []


@pytest.mark.asyncio
async def test_runs_remove_prompt_and_report_truncation():
    result = await get_target_runs(TASK["id"], "owner", "bot", 1, FakeRelay())
    assert result["data"][0]["truncated"] is True
    assert "private prompt" not in str(result)
    assert "error" not in result["data"][0]["runs"][0]


@pytest.mark.asyncio
async def test_partial_disable_is_not_reported_as_success():
    class PartialRelay(FakeRelay):
        async def update_cron(self, **kwargs):
            return {"success": True, "failed_targets": [{"reason": "instance_offline"}]}

    relay = PartialRelay()
    request = DisableCronRequest(
        target_user_id="owner", target_bot_id="bot", task_id=TASK["id"],
        expected_fingerprint=_fingerprint(TASK), improvement_id=123,
        operator="admin", reason="approved cron guard",
    )
    result = await disable_target_cron(request, relay)
    assert result["success"] is False
    assert result["failed_targets"]
