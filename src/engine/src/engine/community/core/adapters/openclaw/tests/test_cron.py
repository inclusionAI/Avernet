"""Unit tests for the OpenClaw cron ACL adapter.

Drives `OpenClawCronAdapter` against a fake `OpenClawCronPort` (a plain
object returning canned raw dicts) — the adapter's job is dict→DTO translation,
request→dict serialisation, get_running_jobs composition, and cron.get None
handling.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from engine.community.core.adapters.openclaw.cron import OpenClawCronAdapter
from engine.community.plugin_api.cron.models import (
    CreateJobRequest,
    CronJob,
    CronNotifyConfig,
    CronNotifyPatch,
    CronRunRecord,
    CronStatus,
    UpdateJobRequest,
)
from engine.community.core.engine.context import AuthContext


# ── helpers ──────────────────────────────────────────────────────────────────


@dataclass
class _FakeAuth:
    token: str | None = None


def _auth(token: str | None = None) -> AuthContext:
    return _FakeAuth(token=token)  # type: ignore[return-value]


def _make_raw_job(
    job_id: str = "job-001",
    name: str = "my-job",
    enabled: bool = True,
    delivery: dict | None = None,
    state: dict | None = None,
    schedule: dict | None = None,
    payload: dict | None = None,
) -> dict[str, Any]:
    return {
        "id": job_id,
        "name": name,
        "enabled": enabled,
        "schedule": schedule or {"kind": "cron", "expr": "0 8 * * *"},
        "payload": payload or {"kind": "agentTurn"},
        "sessionTarget": "isolated",
        "state": state or {},
        "delivery": delivery,
        "createdAtMs": 1000,
        "updatedAtMs": 2000,
    }


def _make_raw_run(
    job_id: str = "job-001",
    status: str = "ok",
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "jobId": job_id,
        "runAtMs": 3000,
        "ts": 4000,
        "status": status,
        "error": error,
        "durationMs": 1000,
        "summary": "done",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


# ── fake port ────────────────────────────────────────────────────────────────


class _FakeCronPort:
    """Fake `OpenClawCronPort` — returns canned dicts; records calls."""

    def __init__(
        self,
        list_result: list[dict] | None = None,
        get_result: dict | None = "MISSING",  # sentinel: None → not found
        status_result: dict | None = None,
        add_result: dict | None = None,
        update_result: dict | None = None,
        remove_result: bool = True,
        run_result: dict | None = None,
        runs_result: list[dict] | None = None,
    ) -> None:
        self._list_result = list_result if list_result is not None else [_make_raw_job()]
        # "MISSING" sentinel means "return the job dict"; None means "not found"
        self._get_result: dict | None | str = get_result
        self._status_result = (
            status_result
            if status_result is not None
            else {
                "running": True,
                "jobCount": 3,
                "enabledCount": 2,
                "nextRunAtMs": 9999,
            }
        )
        self._add_result = add_result or _make_raw_job(job_id="new-job")
        self._update_result = update_result or _make_raw_job(job_id="job-001", name="updated")
        self._remove_result = remove_result
        self._run_result = run_result or {"ran": "job-001"}
        self._runs_result = runs_result if runs_result is not None else [_make_raw_run()]

        # call recorders
        self.list_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.status_calls: list[dict] = []
        self.add_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.remove_calls: list[dict] = []
        self.run_calls: list[dict] = []
        self.runs_calls: list[dict] = []

    async def cron_list(self, include_disabled: bool = True, token: str | None = None) -> list[dict]:
        self.list_calls.append({"include_disabled": include_disabled, "token": token})
        return self._list_result

    async def cron_get(self, job_id: str, token: str | None = None) -> dict | None:
        self.get_calls.append({"job_id": job_id, "token": token})
        if self._get_result == "MISSING":
            # default: return first list entry
            return self._list_result[0] if self._list_result else None
        return self._get_result  # type: ignore[return-value]

    async def cron_status(self, token: str | None = None) -> dict:
        self.status_calls.append({"token": token})
        return self._status_result

    async def cron_add(self, params: dict, token: str | None = None) -> dict:
        self.add_calls.append({"params": params, "token": token})
        return self._add_result

    async def cron_update(self, job_id: str, patch: dict, token: str | None = None) -> dict:
        self.update_calls.append({"job_id": job_id, "patch": patch, "token": token})
        return self._update_result

    async def cron_remove(self, job_id: str, token: str | None = None) -> bool:
        self.remove_calls.append({"job_id": job_id, "token": token})
        return self._remove_result

    async def cron_run(self, job_id: str, mode: str, token: str | None = None) -> dict:
        self.run_calls.append({"job_id": job_id, "mode": mode, "token": token})
        return self._run_result

    async def cron_runs(self, job_id: str, limit: int = 20, token: str | None = None) -> list[dict]:
        self.runs_calls.append({"job_id": job_id, "limit": limit, "token": token})
        return self._runs_result


# ── list_jobs ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_jobs_builds_cron_job_from_raw_dict():
    port = _FakeCronPort(list_result=[_make_raw_job(job_id="j1", name="nightly")])
    adapter = OpenClawCronAdapter(port)
    jobs = await adapter.list_jobs(auth=_auth("tok1"))
    assert len(jobs) == 1
    assert isinstance(jobs[0], CronJob)
    assert jobs[0].id == "j1"
    assert jobs[0].name == "nightly"
    assert port.list_calls[0]["token"] == "tok1"
    assert port.list_calls[0]["include_disabled"] is True


@pytest.mark.asyncio
async def test_list_jobs_normalises_camelcase_keys_recursively():
    # Legacy _convert_cron_job ran recursive camelCase→snake_case over EVERY
    # dict-valued field (schedule / payload / state), + timeout_seconds→timeout_secs.
    raw = _make_raw_job(
        job_id="j9",
        schedule={"kind": "cron", "nextRunAtMs": 111, "nested": {"someKey": 1}},
        state={"runningAtMs": 222, "timeoutSeconds": 30, "inner": {"deepKey": 2}},
        payload={"kind": "agentTurn", "extraCamelKey": "v"},
    )
    port = _FakeCronPort(list_result=[raw])
    adapter = OpenClawCronAdapter(port)
    job = (await adapter.list_jobs())[0]
    # schedule: top-level + nested keys snake-cased (was NOT converted at all pre-fix)
    assert job.schedule["next_run_at_ms"] == 111
    assert job.schedule["nested"] == {"some_key": 1}
    # state: recursive + timeout_seconds → timeout_secs
    assert job.state["running_at_ms"] == 222
    assert job.state["timeout_secs"] == 30
    assert job.state["inner"] == {"deep_key": 2}
    # payload: reverse_mapping + recursive snake_case for other camelCase keys
    assert job.payload["extra_camel_key"] == "v"


@pytest.mark.asyncio
async def test_list_jobs_no_auth_passes_none_token():
    port = _FakeCronPort()
    adapter = OpenClawCronAdapter(port)
    await adapter.list_jobs()
    assert port.list_calls[0]["token"] is None


@pytest.mark.asyncio
async def test_list_jobs_empty_result():
    port = _FakeCronPort(list_result=[])
    adapter = OpenClawCronAdapter(port)
    assert await adapter.list_jobs() == []


# ── get_job ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_job_builds_cron_job_when_found():
    raw = _make_raw_job(job_id="j2", name="weekly")
    port = _FakeCronPort(get_result=raw)
    adapter = OpenClawCronAdapter(port)
    job = await adapter.get_job("j2", auth=_auth("tok2"))
    assert isinstance(job, CronJob)
    assert job.id == "j2"
    assert job.name == "weekly"
    assert port.get_calls[0]["job_id"] == "j2"
    assert port.get_calls[0]["token"] == "tok2"


@pytest.mark.asyncio
async def test_get_job_returns_none_when_not_found():
    port = _FakeCronPort(get_result=None)
    adapter = OpenClawCronAdapter(port)
    result = await adapter.get_job("missing-job")
    assert result is None


# ── get_status ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_status_builds_cron_status():
    port = _FakeCronPort()
    adapter = OpenClawCronAdapter(port)
    status = await adapter.get_status(auth=_auth("tok3"))
    assert isinstance(status, CronStatus)
    assert status.running is True
    assert status.job_count == 3
    assert status.enabled_count == 2
    assert status.next_run_at_ms == 9999
    assert port.status_calls[0]["token"] == "tok3"


@pytest.mark.asyncio
async def test_get_status_empty_payload_returns_defaults():
    port = _FakeCronPort(status_result={})
    adapter = OpenClawCronAdapter(port)
    status = await adapter.get_status()
    assert status.running is False
    assert status.job_count == 0
    assert status.enabled_count == 0
    assert status.next_run_at_ms is None


# ── add_job ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_job_serialises_request_and_returns_cron_job():
    port = _FakeCronPort(add_result=_make_raw_job(job_id="new-001", name="hourly"))
    adapter = OpenClawCronAdapter(port)
    request = CreateJobRequest(
        name="hourly",
        schedule={"kind": "cron", "expr": "0 * * * *"},
        payload={"kind": "agentTurn"},
        enabled=True,
        notify=None,
    )
    job = await adapter.add_job(request, auth=_auth("tok4"))
    assert isinstance(job, CronJob)
    assert job.id == "new-001"
    call = port.add_calls[0]
    assert call["token"] == "tok4"
    params = call["params"]
    assert params["name"] == "hourly"
    assert params["schedule"] == {"kind": "cron", "expr": "0 * * * *"}
    # delivery should be present (no notify → mode none, no accountId)
    assert params["delivery"]["mode"] == "none"
    assert "accountId" not in params["delivery"]
    assert "to" not in params["delivery"]


@pytest.mark.asyncio
async def test_add_job_with_notify_enabled_sets_account_id():
    port = _FakeCronPort(add_result=_make_raw_job())
    adapter = OpenClawCronAdapter(port)
    request = CreateJobRequest(
        name="notified",
        schedule={"kind": "cron", "expr": "0 9 * * *"},
        payload={"kind": "agentTurn"},
        notify=CronNotifyConfig(enabled=True, user_ids=["u1", "u2"]),
    )
    await adapter.add_job(request)
    params = port.add_calls[0]["params"]
    assert params["delivery"]["accountId"] == "u1,u2"
    assert "to" not in params["delivery"]


@pytest.mark.asyncio
async def test_add_job_with_notify_no_users_sets_empty_sentinel():
    port = _FakeCronPort(add_result=_make_raw_job())
    adapter = OpenClawCronAdapter(port)
    request = CreateJobRequest(
        name="notified-empty",
        schedule={"kind": "cron", "expr": "0 10 * * *"},
        payload={"kind": "agentTurn"},
        notify=CronNotifyConfig(enabled=True, user_ids=[]),
    )
    await adapter.add_job(request)
    params = port.add_calls[0]["params"]
    assert params["delivery"]["accountId"] == "__empty__"
    assert "to" not in params["delivery"]


@pytest.mark.asyncio
async def test_add_job_with_notify_disabled_uses_account_id_without_empty_to():
    port = _FakeCronPort(add_result=_make_raw_job())
    adapter = OpenClawCronAdapter(port)
    request = CreateJobRequest(
        name="notify-off",
        schedule={"kind": "cron", "expr": "0 11 * * *"},
        payload={"kind": "agentTurn"},
        notify=CronNotifyConfig(enabled=False, user_ids=[]),
    )
    await adapter.add_job(request)
    params = port.add_calls[0]["params"]
    assert params["delivery"] == {"mode": "none", "accountId": "__disabled__"}


@pytest.mark.asyncio
async def test_add_job_converts_at_schedule_to_once():
    port = _FakeCronPort(add_result=_make_raw_job())
    adapter = OpenClawCronAdapter(port)
    request = CreateJobRequest(
        name="once",
        schedule={"kind": "at", "at_ms": 12345},
        payload={"kind": "agentTurn"},
    )
    await adapter.add_job(request)
    params = port.add_calls[0]["params"]
    assert params["schedule"] == {"kind": "once", "atMs": 12345}


# ── update_job ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_job_sends_patch_and_returns_cron_job():
    updated_raw = _make_raw_job(job_id="j1", name="renamed")
    port = _FakeCronPort(update_result=updated_raw)
    adapter = OpenClawCronAdapter(port)
    request = UpdateJobRequest(name="renamed")
    job = await adapter.update_job("j1", request, auth=_auth("tok5"))
    assert isinstance(job, CronJob)
    assert job.name == "renamed"
    call = port.update_calls[0]
    assert call["job_id"] == "j1"
    assert call["patch"]["name"] == "renamed"
    assert call["token"] == "tok5"


@pytest.mark.asyncio
async def test_update_job_raises_on_empty_patch():
    port = _FakeCronPort()
    adapter = OpenClawCronAdapter(port)
    with pytest.raises(ValueError, match="No fields to update"):
        await adapter.update_job("j1", UpdateJobRequest())


@pytest.mark.asyncio
async def test_update_job_notify_disable_encodes_users():
    existing_raw = _make_raw_job(
        delivery={"mode": "none", "accountId": "u1,u2"}
    )
    updated_raw = _make_raw_job()
    port = _FakeCronPort(get_result=existing_raw, update_result=updated_raw)
    adapter = OpenClawCronAdapter(port)
    request = UpdateJobRequest(notify=CronNotifyPatch(enabled=False))
    await adapter.update_job("job-001", request)
    call = port.update_calls[0]
    delivery = call["patch"]["delivery"]
    # existing users u1,u2 should be encoded in __disabled__
    assert delivery["accountId"].startswith("__disabled__")
    assert "u1" in delivery["accountId"]


@pytest.mark.asyncio
async def test_update_job_payload_injects_kind_from_existing():
    existing_raw = _make_raw_job(payload={"kind": "systemEvent", "text": "hello"})
    updated_raw = _make_raw_job()
    port = _FakeCronPort(get_result=existing_raw, update_result=updated_raw)
    adapter = OpenClawCronAdapter(port)
    # payload patch without "kind" → adapter should look up existing
    request = UpdateJobRequest(payload={"timeout_secs": 30})
    await adapter.update_job("job-001", request)
    patch = port.update_calls[0]["patch"]
    assert patch["payload"]["kind"] == "systemEvent"


# ── remove_job ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_job_returns_true_on_success():
    port = _FakeCronPort(remove_result=True)
    adapter = OpenClawCronAdapter(port)
    result = await adapter.remove_job("j1", auth=_auth("tok6"))
    assert result is True
    assert port.remove_calls[0]["job_id"] == "j1"
    assert port.remove_calls[0]["token"] == "tok6"


@pytest.mark.asyncio
async def test_remove_job_returns_false_on_failure():
    port = _FakeCronPort(remove_result=False)
    adapter = OpenClawCronAdapter(port)
    result = await adapter.remove_job("j1")
    assert result is False


# ── run_job ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_job_force_passes_force_mode():
    port = _FakeCronPort(run_result={"ran": "j1", "ok": True})
    adapter = OpenClawCronAdapter(port)
    result = await adapter.run_job("j1", force=True, auth=_auth("tok7"))
    assert result["ran"] == "j1"
    call = port.run_calls[0]
    assert call["mode"] == "force"
    assert call["job_id"] == "j1"
    assert call["token"] == "tok7"


@pytest.mark.asyncio
async def test_run_job_non_force_passes_due_mode():
    port = _FakeCronPort()
    adapter = OpenClawCronAdapter(port)
    await adapter.run_job("j1", force=False)
    assert port.run_calls[0]["mode"] == "due"


# ── get_runs ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_runs_builds_cron_run_records():
    raw_run = _make_raw_run(job_id="j1", status="ok")
    port = _FakeCronPort(runs_result=[raw_run])
    adapter = OpenClawCronAdapter(port)
    records = await adapter.get_runs("j1", limit=5, auth=_auth("tok8"))
    assert len(records) == 1
    assert isinstance(records[0], CronRunRecord)
    assert records[0].job_id == "j1"
    assert records[0].status == "ok"
    assert records[0].started_at_ms == 3000
    assert records[0].finished_at_ms == 4000
    assert records[0].duration_ms == 1000
    assert records[0].output == "done"
    assert records[0].input_tokens == 10
    assert records[0].output_tokens == 5
    call = port.runs_calls[0]
    assert call["job_id"] == "j1"
    assert call["limit"] == 5
    assert call["token"] == "tok8"


@pytest.mark.asyncio
async def test_get_runs_empty_result():
    port = _FakeCronPort(runs_result=[])
    adapter = OpenClawCronAdapter(port)
    assert await adapter.get_runs("j1") == []


# ── get_running_jobs ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_running_jobs_filters_by_running_at_ms():
    running_job = _make_raw_job(
        job_id="running-1", name="active", state={"running_at_ms": 5000}
    )
    idle_job = _make_raw_job(
        job_id="idle-1", name="idle", state={"last_run_at_ms": 4000}
    )
    port = _FakeCronPort(list_result=[running_job, idle_job])
    adapter = OpenClawCronAdapter(port)
    running = await adapter.get_running_jobs(auth=_auth("tok9"))
    assert len(running) == 1
    assert running[0]["id"] == "running-1"
    assert running[0]["name"] == "active"
    assert running[0]["running_at_ms"] == 5000


@pytest.mark.asyncio
async def test_get_running_jobs_empty_when_none_running():
    idle_job = _make_raw_job(state={"last_run_at_ms": 100})
    port = _FakeCronPort(list_result=[idle_job])
    adapter = OpenClawCronAdapter(port)
    running = await adapter.get_running_jobs()
    assert running == []


@pytest.mark.asyncio
async def test_get_running_jobs_calls_list_with_token():
    port = _FakeCronPort(list_result=[])
    adapter = OpenClawCronAdapter(port)
    await adapter.get_running_jobs(auth=_auth("tok10"))
    assert port.list_calls[0]["token"] == "tok10"


# ── delivery → notify parsing ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_jobs_parses_disabled_delivery():
    raw = _make_raw_job(
        delivery={"mode": "none", "accountId": "__disabled__u1,u2"}
    )
    port = _FakeCronPort(list_result=[raw])
    adapter = OpenClawCronAdapter(port)
    jobs = await adapter.list_jobs()
    notify = jobs[0].notify
    assert notify is not None
    assert notify.enabled is False
    assert "u1" in notify.user_ids
    assert "u2" in notify.user_ids


@pytest.mark.asyncio
async def test_list_jobs_parses_empty_sentinel_delivery():
    raw = _make_raw_job(delivery={"mode": "none", "accountId": "__empty__"})
    port = _FakeCronPort(list_result=[raw])
    adapter = OpenClawCronAdapter(port)
    jobs = await adapter.list_jobs()
    notify = jobs[0].notify
    assert notify is not None
    assert notify.enabled is True
    assert notify.user_ids == []


@pytest.mark.asyncio
async def test_list_jobs_no_delivery_gives_no_notify():
    raw = _make_raw_job(delivery=None)
    port = _FakeCronPort(list_result=[raw])
    adapter = OpenClawCronAdapter(port)
    jobs = await adapter.list_jobs()
    assert jobs[0].notify is None
