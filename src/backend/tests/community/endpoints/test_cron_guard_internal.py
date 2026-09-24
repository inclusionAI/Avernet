"""Cron Guard M2M API must fail closed and preserve the existing relay semantics."""

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.cron.internal_auth import verify_cron_guard_token
from agentclaw.community.adapters.http.cron.internal_router import (
    DisableCronRequest,
    _fingerprint,
    disable_target_cron,
    get_target_runs,
    list_target_crons,
)
from agentclaw.community.di.config import CronGuardInternalToken


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
