"""ClaudeCode cron ACL adapter.

Implements the core `CronService` by delegating to an injected
`ClaudeCodeCronPort`. The adapter:
  - extracts `auth.token` and passes it as the routing key (pooled);
  - owns ALL dict→DTO construction (CronJob / CronStatus / CronRunRecord)
    relocated from `engines/claude_code/cron.py`;
  - serializes CreateJobRequest / UpdateJobRequest → plain dicts before
    handing them to the port (write path);
  - implements `get_running_jobs` as adapter-side composition over `list_jobs`
    — no dedicated port method (the port exposes `cron_get_running_jobs`, but
    the corp impl composed it from `list_jobs` + state.running_at_ms; we use
    the port method for fidelity with the port contract).

The translation helpers mirror the corp impl; the OpenClaw cron adapter has
extra notify-encoding logic (``_build_update_patch`` / delivery mapping) that
the corp claude_code impl also implements — those live here too.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from engine.community.core.cron.protocol import CronService
from engine.community.core.engine.context import AuthContext
from engine.community.plugin_api.claude_code.cron import ClaudeCodeCronPort
from engine.community.plugin_api.cron.models import (
    CreateJobRequest,
    CronJob,
    CronNotifyConfig,
    CronRunRecord,
    CronStatus,
    UpdateJobRequest,
)

log = logging.getLogger("claude-code-cron-adapter")


# ── camelCase ↔ snake_case helpers (relocated from corp service) ─────────────


_CAMEL_RE_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RE_2 = re.compile(r"([a-z0-9])([A-Z])")


def _camel_to_snake(name: str) -> str:
    return _CAMEL_RE_2.sub(r"\1_\2", _CAMEL_RE_1.sub(r"\1_\2", name)).lower()


def _convert_dict_keys(obj: Any) -> Any:
    """Recursively convert dict keys from camelCase to snake_case."""
    if isinstance(obj, dict):
        return {_camel_to_snake(k): _convert_dict_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_dict_keys(x) for x in obj]
    return obj


def _convert_payload_keys(payload: dict) -> dict:
    """Translate relay camelCase payload keys to OCB snake_case.

    Explicit rename: ``timeoutSeconds`` → ``timeout_secs`` (not the naïve
    ``timeout_seconds``).
    """
    if not isinstance(payload, dict):
        return payload
    rename = {"timeoutSeconds": "timeout_secs"}
    out: dict[str, Any] = {}
    for k, v in payload.items():
        out[rename.get(k, _camel_to_snake(k))] = v
    return out


def _convert_payload_for_wire(payload: dict) -> dict:
    """Reverse: OCB snake_case → relay camelCase payload keys."""
    if not isinstance(payload, dict):
        return payload
    rename = {
        "timeout_secs": "timeoutSeconds",
        "timeout_sec": "timeoutSeconds",
    }
    out: dict[str, Any] = {}
    for k, v in payload.items():
        out[rename.get(k, k)] = v
    return out


# ── raw-dict → core-DTO builders (relocated from corp service) ───────────────


def _job_from_dict(data: dict[str, Any]) -> CronJob:
    """Build a `CronJob` from a raw relay job dict.

    Relocated from ``ClaudeCodeCronService._relay_job_to_cronjob``.
    """
    schedule = _convert_dict_keys(data.get("schedule") or {})
    payload = _convert_payload_keys(data.get("payload") or {})
    state = _convert_dict_keys(data.get("state") or {})

    notify: CronNotifyConfig | None = None
    delivery = data.get("delivery")
    if isinstance(delivery, dict):
        account_id = str(delivery.get("accountId") or "")
        if account_id.startswith("__disabled__"):
            tail = account_id[len("__disabled__"):]
            user_ids = [uid.strip() for uid in tail.split(",") if uid.strip()]
            notify = CronNotifyConfig(enabled=False, user_ids=user_ids)
        elif account_id == "__empty__":
            notify = CronNotifyConfig(enabled=True, user_ids=[])
        elif account_id:
            user_ids = [uid.strip() for uid in account_id.split(",") if uid.strip()]
            notify = CronNotifyConfig(enabled=True, user_ids=user_ids)

    return CronJob(
        id=str(data.get("id", "")),
        name=str(data.get("name", "")),
        enabled=bool(data.get("enabled", True)),
        schedule=schedule,
        payload=payload,
        session_target=str(data.get("sessionTarget", "isolated")),
        state=state,
        notify=notify,
        created_at_ms=int(data.get("createdAtMs", 0)),
        updated_at_ms=int(data.get("updatedAtMs", 0)),
    )


def _run_from_dict(data: dict[str, Any]) -> CronRunRecord:
    """Build a `CronRunRecord` from a raw relay run-record dict.

    Relocated from ``ClaudeCodeCronService._relay_run_to_record``.
    """
    usage = data.get("usage") or {}
    return CronRunRecord(
        job_id=str(data.get("jobId", "")),
        started_at_ms=int(data.get("runAtMs", 0)),
        finished_at_ms=int(data.get("ts", 0)),
        status=str(data.get("status", "error")),
        error=data.get("error"),
        duration_ms=int(data.get("durationMs", 0)),
        output=data.get("summary"),
        input_tokens=usage.get("input_tokens") if isinstance(usage, dict) else None,
        output_tokens=usage.get("output_tokens") if isinstance(usage, dict) else None,
    )


def _status_from_dict(data: dict[str, Any]) -> CronStatus:
    """Build a `CronStatus` from a raw cron.status payload dict."""
    return CronStatus(
        running=bool(data.get("running", False)),
        job_count=int(data.get("jobCount", 0)),
        enabled_count=int(data.get("enabledCount", 0)),
        next_run_at_ms=data.get("nextRunAtMs"),
    )


# ── request → dict serialisers (write-path, relocated from corp service) ─────


def _tz(schedule: dict) -> dict:
    tz = schedule.get("tz")
    return {"tz": tz} if tz else {}


def _schedule_for_wire(schedule: dict) -> dict:
    """Translate OCB snake_case schedule to relay camelCase.

    Maps legacy ``at`` kind (moltis/openclaw) to the relay's ``once``.
    """
    if not isinstance(schedule, dict):
        return schedule
    kind = schedule.get("kind")
    if kind in ("at", "once"):
        at_ms = schedule.get("at_ms") or schedule.get("atMs")
        return {"kind": "once", "atMs": at_ms, **_tz(schedule)}
    if kind == "every":
        every_ms = schedule.get("every_ms") or schedule.get("everyMs")
        first = schedule.get("first_run_at_ms") or schedule.get("firstRunAtMs")
        out: dict[str, Any] = {"kind": "every", "everyMs": every_ms, **_tz(schedule)}
        if first is not None:
            out["firstRunAtMs"] = first
        return out
    if kind == "cron":
        return {"kind": "cron", "expr": schedule.get("expr"), **_tz(schedule)}
    return schedule


def _notify_to_delivery(notify: CronNotifyConfig | None) -> dict:
    """Encode ``CronNotifyConfig`` into the relay's ``delivery`` dict."""
    if notify is None:
        return {"mode": "none", "to": ""}
    if notify.enabled is False:
        tail = ",".join(notify.user_ids or [])
        return {"mode": "none", "accountId": f"__disabled__{tail}"}
    if notify.user_ids:
        return {"mode": "none", "to": "", "accountId": ",".join(notify.user_ids)}
    return {"mode": "none", "to": "", "accountId": "__empty__"}


def _build_add_params(request: CreateJobRequest) -> dict[str, Any]:
    """Build the ``cron.add`` params dict from a ``CreateJobRequest``."""
    return {
        "name": request.name,
        "schedule": _schedule_for_wire(request.schedule),
        "payload": _convert_payload_for_wire(request.payload),
        "sessionTarget": request.session_target,
        "enabled": request.enabled,
        "delivery": _notify_to_delivery(request.notify),
    }


def _build_update_patch(
    request: UpdateJobRequest,
    existing_job: CronJob | None,
) -> dict[str, Any]:
    """Build the ``cron.update`` patch dict from an ``UpdateJobRequest``."""
    patch: dict[str, Any] = {}
    if request.name is not None:
        patch["name"] = request.name
    if request.schedule is not None:
        patch["schedule"] = _schedule_for_wire(request.schedule)
    if request.payload is not None:
        patch["payload"] = _convert_payload_for_wire(request.payload)
    if request.enabled is not None:
        patch["enabled"] = request.enabled
    if request.notify is not None:
        cur_enabled = (
            existing_job.notify.enabled
            if existing_job and existing_job.notify
            else True
        )
        cur_user_ids = (
            existing_job.notify.user_ids
            if existing_job and existing_job.notify
            else []
        ) or []
        new_enabled = (
            request.notify.enabled
            if request.notify.enabled is not None
            else cur_enabled
        )
        new_user_ids = (
            request.notify.user_ids
            if request.notify.user_ids is not None
            else cur_user_ids
        )
        patch["delivery"] = _notify_to_delivery(
            CronNotifyConfig(enabled=bool(new_enabled), user_ids=new_user_ids)
        )
    return patch


# ── Adapter ──────────────────────────────────────────────────────────────────


class ClaudeCodeCronAdapter(CronService):
    """`CronService` over the claude_code native cron port."""

    def __init__(self, port: ClaudeCodeCronPort) -> None:
        self._port = port

    async def list_jobs(
        self, auth: AuthContext | None = None,
    ) -> list[CronJob]:
        token = auth.token if auth is not None else None
        log.info("[list_jobs] fetching job list")
        raw_jobs = await self._port.cron_list_jobs(token=token)
        jobs = [_job_from_dict(j) for j in raw_jobs if isinstance(j, dict)]
        log.info("[list_jobs] returning %d jobs", len(jobs))
        return jobs

    async def get_job(
        self, job_id: str, auth: AuthContext | None = None,
    ) -> CronJob | None:
        token = auth.token if auth is not None else None
        log.debug("[get_job] job_id=%s", job_id)
        raw = await self._port.cron_get_job(job_id=job_id, token=token)
        if raw is None:
            return None
        return _job_from_dict(raw)

    async def get_status(
        self, auth: AuthContext | None = None,
    ) -> CronStatus:
        token = auth.token if auth is not None else None
        log.info("[get_status] fetching cron status")
        raw = await self._port.cron_get_status(token=token)
        return _status_from_dict(raw)

    async def add_job(
        self, request: CreateJobRequest, auth: AuthContext | None = None,
    ) -> CronJob:
        token = auth.token if auth is not None else None
        log.info("[add_job] name=%s", request.name)
        params = _build_add_params(request)
        raw = await self._port.cron_add_job(job=params, token=token)
        job = _job_from_dict(raw)
        log.info("[add_job] created job: %s", job.id)
        return job

    async def update_job(
        self,
        job_id: str,
        request: UpdateJobRequest,
        auth: AuthContext | None = None,
    ) -> CronJob:
        token = auth.token if auth is not None else None
        log.info("[update_job] job_id=%s", job_id)

        needs_existing = request.notify is not None
        existing_job: CronJob | None = None
        if needs_existing:
            existing_job = await self.get_job(job_id, auth=auth)

        patch = _build_update_patch(request, existing_job)
        if not patch:
            log.warning("[update_job] no fields to update")
            raise ValueError("No fields to update")

        raw = await self._port.cron_update_job(
            job_id=job_id, patch=patch, token=token
        )
        job = _job_from_dict(raw)
        log.info("[update_job] updated job: %s", job_id)
        return job

    async def remove_job(
        self, job_id: str, auth: AuthContext | None = None,
    ) -> bool:
        token = auth.token if auth is not None else None
        log.info("[remove_job] job_id=%s", job_id)
        return await self._port.cron_remove_job(job_id=job_id, token=token)

    async def run_job(
        self,
        job_id: str,
        force: bool = False,
        timeout: int | None = None,
        auth: AuthContext | None = None,
    ) -> dict:
        token = auth.token if auth is not None else None
        log.info("[run_job] job_id=%s force=%s", job_id, force)
        raw = await self._port.cron_run_job(job_id=job_id, token=token)
        # The corp impl returned resp.payload; the port returns the raw dict.
        # Surface it as-is (success/non-success callers can inspect it).
        return raw

    async def get_runs(
        self,
        job_id: str,
        limit: int = 20,
        auth: AuthContext | None = None,
    ) -> list[CronRunRecord]:
        token = auth.token if auth is not None else None
        log.info("[get_runs] job_id=%s limit=%s", job_id, limit)
        raw_runs = await self._port.cron_get_runs(
            job_id=job_id, limit=limit, token=token
        )
        records = [_run_from_dict(r) for r in raw_runs if isinstance(r, dict)]
        log.info("[get_runs] returning %d records", len(records))
        return records

    async def get_running_jobs(
        self, auth: AuthContext | None = None,
    ) -> list[dict]:
        """List currently-executing jobs.

        Delegates to the port's ``cron_get_running_jobs`` (impl-side filter
        from status). Falls back to adapter-side composition over ``list_jobs``
        when the port returns an empty list but jobs exist with
        ``state.running_at_ms`` — matching the corp impl's behaviour.
        """
        token = auth.token if auth is not None else None
        raw = await self._port.cron_get_running_jobs(token=token)
        if isinstance(raw, list) and raw:
            return raw

        # Adapter-side fallback — mirrors the corp impl's composition path.
        jobs = await self.list_jobs(auth=auth)
        running: list[dict] = []
        for job in jobs:
            if job.state and "running_at_ms" in job.state:
                running.append({
                    "id": job.id,
                    "name": job.name,
                    "running_at_ms": job.state["running_at_ms"],
                })
        return running


__all__ = ["ClaudeCodeCronAdapter"]
