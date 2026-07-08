"""OpenClaw cron ACL adapter.

Implements the core `CronService` by delegating to an injected
`OpenClawCronPort`.  The adapter:
  - extracts `auth.token` and passes it as the routing key (pooled);
  - owns ALL dict→DTO construction (CronJob / CronStatus / CronRunRecord)
    relocated from `engines/openclaw/cron.py`;
  - serializes CreateJobRequest / UpdateJobRequest → plain dicts before
    handing them to the port (write path);
  - implements `get_running_jobs` as adapter-side composition over `cron_list`
    — no dedicated port method (design decision 6 / port catalog).
"""
from __future__ import annotations

import logging
import re
from typing import Any

from engine.community.plugin_api.cron.models import (
    CreateJobRequest,
    CronJob,
    CronNotifyConfig,
    CronRunRecord,
    CronStatus,
    UpdateJobRequest,
)
from engine.community.core.cron.protocol import CronService
from engine.community.core.engine.context import AuthContext
from engine.community.plugin_api.openclaw.cron import OpenClawCronPort

log = logging.getLogger("openclaw-cron-adapter")


# ── camelCase ↔ snake_case helpers (relocated from legacy service) ──────────


def _camel_to_snake(name: str) -> str:
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _convert_dict_keys(obj: Any) -> Any:
    """Recursively convert dict keys camelCase → snake_case (lists too).

    Relocated from the legacy `engines/openclaw/cron.py:_convert_dict_keys`.
    """
    if isinstance(obj, dict):
        return {_camel_to_snake(k): _convert_dict_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_dict_keys(i) for i in obj]
    return obj


def _normalize_job_dict_field(d: dict[str, Any]) -> dict[str, Any]:
    """Recursive camelCase→snake_case + `timeout_seconds → timeout_secs`.

    Reproduces the per-field body of the legacy `_convert_cron_job`, which
    applied this to EVERY dict-valued CronJob field (schedule / payload / state),
    recursively — not just the top-level `state` keys.
    """
    converted = _convert_dict_keys(d)
    if isinstance(converted, dict) and "timeout_seconds" in converted:
        converted["timeout_secs"] = converted.pop("timeout_seconds")
    return converted


# ── raw-dict → core-DTO builders (relocated from legacy service) ─────────────


def _job_from_dict(data: dict[str, Any]) -> CronJob:
    """Build a `CronJob` from a raw OpenClaw gateway job dict.

    Relocated from `OpenClawCronService._openclaw_job_to_cronjob` +
    the follow-up `_convert_cron_job` camelCase normalisation — both steps
    are merged here so the legacy two-pass behaviour is preserved in one call.
    """
    # ── notify / delivery ────────────────────────────────────────────────────
    notify: CronNotifyConfig | None = None
    delivery = data.get("delivery")
    job_id_short = data.get("id", "")[:8]
    job_name = data.get("name", "")
    log.info(
        f"[_job_from_dict] job={job_name}({job_id_short}), delivery={delivery}"
    )
    if delivery is not None:
        account_id = delivery.get("accountId", "")
        if account_id and account_id.startswith("__disabled__"):
            is_enabled = False
            encoded_users = account_id[len("__disabled__"):]
            user_ids = [
                uid.strip() for uid in encoded_users.split(",") if uid.strip()
            ]
        elif account_id == "__empty__":
            is_enabled = True
            user_ids = []
        else:
            user_ids = (
                [uid.strip() for uid in account_id.split(",")] if account_id else []
            )
            mode = delivery.get("mode", "")
            if mode == "none" and not account_id:
                is_enabled = False
            else:
                is_enabled = bool(account_id) or mode == "webhook"
        notify = CronNotifyConfig(enabled=is_enabled, user_ids=user_ids)
    log.info(
        f"[_job_from_dict] job={job_name}({job_id_short}), parsed notify={notify}"
    )

    # ── payload camelCase → snake_case field renaming ────────────────────────
    payload = data.get("payload") or {}
    if payload:
        reverse_mapping = {
            "timeoutSeconds": "timeout_secs",
            "bestEffort": "best_effort",
            "bestEffortDeliver": "best_effort_deliver",
            "allowUnsafeExternalContent": "allow_unsafe",
            "lightContext": "light_context",
        }
        converted_payload: dict[str, Any] = {}
        for key, value in payload.items():
            new_key = reverse_mapping.get(key, key)
            converted_payload[new_key] = value
        payload = converted_payload

    # Legacy ran _convert_cron_job over EVERY dict-valued field recursively
    # (schedule / payload / state) — reproduce that here, not just shallow state.
    payload = _normalize_job_dict_field(payload)
    state = _normalize_job_dict_field(data.get("state") or {})
    schedule = _normalize_job_dict_field(data.get("schedule") or {})

    return CronJob(
        id=data.get("id", ""),
        name=data.get("name", ""),
        enabled=data.get("enabled", True),
        schedule=schedule,
        payload=payload,
        session_target=data.get("sessionTarget", "isolated"),
        state=state,
        notify=notify,
        created_at_ms=data.get("createdAtMs", 0),
        updated_at_ms=data.get("updatedAtMs", 0),
    )


def _run_from_dict(data: dict[str, Any]) -> CronRunRecord:
    """Build a `CronRunRecord` from a raw OpenClaw run-record dict.

    Relocated from `OpenClawCronService._openclaw_run_to_record` +
    `_convert_cron_run_record`.  The model is constructed directly from
    already-mapped field names — no dump→convert→rebuild needed because all
    camelCase→snake_case mapping is done via explicit field access above.
    """
    usage = data.get("usage") or {}
    return CronRunRecord(
        job_id=data.get("jobId", ""),
        started_at_ms=data.get("runAtMs", 0),
        finished_at_ms=data.get("ts", 0),
        status=data.get("status", "error"),
        error=data.get("error"),
        duration_ms=data.get("durationMs", 0),
        output=data.get("summary"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
    )


def _status_from_dict(data: dict[str, Any]) -> CronStatus:
    """Build a `CronStatus` from a raw cron.status payload dict.

    Relocated from `OpenClawCronService.get_status` inline build +
    `_convert_cron_status`.  The model is constructed directly from
    already-mapped field names — no dump→convert→rebuild needed.
    """
    return CronStatus(
        running=data.get("running", False),
        job_count=data.get("jobCount", 0),
        enabled_count=data.get("enabledCount", 0),
        next_run_at_ms=data.get("nextRunAtMs"),
    )


# ── request → dict serialisers (write-path; relocated from legacy service) ───


def _convert_payload_to_openclaw(payload: dict[str, Any]) -> dict[str, Any]:
    """Serialize a payload dict from snake_case to OpenClaw camelCase.

    Relocated from `OpenClawCronService._convert_payload_to_openclaw`.
    """
    if not isinstance(payload, dict):
        return payload

    field_mapping = {
        "timeout_secs": "timeoutSeconds",
        "timeout_sec": "timeoutSeconds",
        "best_effort": "bestEffort",
        "best_effort_deliver": "bestEffortDeliver",
        "allow_unsafe": "allowUnsafeExternalContent",
        "light_context": "lightContext",
    }

    result: dict[str, Any] = {}
    kind = payload.get("kind", "agentTurn")

    for key, value in payload.items():
        if kind == "agentTurn":
            if key == "text":
                continue
        elif kind == "systemEvent":
            if key not in ("kind", "text"):
                continue
        new_key = field_mapping.get(key, key)
        result[new_key] = value

    return result


def _convert_schedule_to_openclaw(schedule: dict[str, Any]) -> dict[str, Any]:
    """Serialize a schedule dict to OpenClaw format.

    Relocated from `OpenClawCronService._convert_schedule_to_openclaw`.
    """
    if not isinstance(schedule, dict):
        return schedule

    kind = schedule.get("kind")
    if kind == "at":
        return {
            "kind": "once",
            "atMs": schedule.get("at_ms") or schedule.get("atMs"),
        }
    if kind == "every":
        return {
            "kind": "every",
            "everyMs": schedule.get("every_ms") or schedule.get("everyMs"),
        }
    # "cron" and unknowns pass through unchanged
    return schedule


def _build_add_params(request: CreateJobRequest) -> dict[str, Any]:
    """Build the `cron.add` params dict from a `CreateJobRequest`.

    Relocated from `OpenClawCronService.add_job` — schedule/payload conversion
    + delivery assembly now live here (adapter side).
    """
    openclaw_payload = _convert_payload_to_openclaw(request.payload)
    openclaw_schedule = _convert_schedule_to_openclaw(request.schedule)

    job_data: dict[str, Any] = {
        "name": request.name,
        "schedule": openclaw_schedule,
        "payload": openclaw_payload,
        "sessionTarget": request.session_target,
        "enabled": request.enabled,
    }

    delivery: dict[str, Any] = {"mode": "none"}
    if request.notify is not None:
        if request.notify.enabled:
            account_id = (
                ",".join(request.notify.user_ids)
                if request.notify.user_ids
                else "__empty__"
            )
        else:
            encoded_users = ",".join(request.notify.user_ids or [])
            account_id = f"__disabled__{encoded_users}"
        delivery["accountId"] = account_id

    job_data["delivery"] = delivery

    return job_data


def _build_update_patch(
    request: UpdateJobRequest,
    existing_job: CronJob | None,
) -> dict[str, Any]:
    """Build the `cron.update` patch dict from an `UpdateJobRequest`.

    Relocated from `OpenClawCronService.update_job` — patch assembly +
    notify/delivery conversion now live here (adapter side).
    `existing_job` is pre-fetched by the adapter when needed (payload-kind
    fallback and notify merge).
    """
    patch: dict[str, Any] = {}

    if request.name is not None:
        patch["name"] = request.name
    if request.schedule is not None:
        patch["schedule"] = _convert_schedule_to_openclaw(request.schedule)
    if request.payload is not None:
        patch["payload"] = _convert_payload_to_openclaw(request.payload)
        if "kind" not in patch["payload"]:
            original_kind = (
                existing_job.payload.get("kind", "agentTurn")
                if existing_job
                else "agentTurn"
            )
            patch["payload"]["kind"] = original_kind
    if request.enabled is not None:
        patch["enabled"] = request.enabled
    if request.notify is not None:
        existing_enabled = False
        existing_user_ids: list[str] = []
        if existing_job and existing_job.notify:
            existing_enabled = existing_job.notify.enabled
            existing_user_ids = existing_job.notify.user_ids or []

        new_enabled = (
            request.notify.enabled
            if request.notify.enabled is not None
            else existing_enabled
        )
        new_user_ids = (
            request.notify.user_ids
            if request.notify.user_ids is not None
            else existing_user_ids
        )
        log.info(
            f"[_build_update_patch] notify update: enabled={new_enabled}, user_ids={new_user_ids}"
        )

        if new_enabled is False:
            encoded_users = ",".join(new_user_ids) if new_user_ids else ""
            patch["delivery"] = {
                "mode": "none",
                "accountId": f"__disabled__{encoded_users}",
            }
        else:
            account_id = ",".join(new_user_ids) if new_user_ids else "__empty__"
            patch["delivery"] = {"mode": "none", "accountId": account_id}

    return patch


# ── Adapter ──────────────────────────────────────────────────────────────────


class OpenClawCronAdapter(CronService):
    """`CronService` over the OpenClaw native port."""

    def __init__(self, port: OpenClawCronPort) -> None:
        self._port = port

    async def list_jobs(
        self, auth: AuthContext | None = None,
    ) -> list[CronJob]:
        """List all cron jobs via `cron.list`."""
        token = auth.token if auth is not None else None
        log.info("[list_jobs] fetching job list")
        raw_jobs = await self._port.cron_list(include_disabled=True, token=token)
        jobs = [_job_from_dict(j) for j in raw_jobs]
        log.info(f"[list_jobs] returning {len(jobs)} jobs")
        return jobs

    async def get_job(
        self, job_id: str, auth: AuthContext | None = None,
    ) -> CronJob | None:
        """Look up a single job by id (feature-probed cron.get or list scan)."""
        token = auth.token if auth is not None else None
        log.debug(f"[get_job] job_id={job_id}")
        raw = await self._port.cron_get(job_id=job_id, token=token)
        if raw is None:
            return None
        return _job_from_dict(raw)

    async def get_status(
        self, auth: AuthContext | None = None,
    ) -> CronStatus:
        """Report the cron service's overall status via `cron.status`."""
        token = auth.token if auth is not None else None
        log.info("[get_status] fetching cron status")
        raw = await self._port.cron_status(token=token)
        status = _status_from_dict(raw)
        log.info(
            f"[get_status] running={status.running}, jobs={status.job_count}"
        )
        return status

    async def add_job(
        self, request: CreateJobRequest, auth: AuthContext | None = None,
    ) -> CronJob:
        """Create a new cron job via `cron.add`."""
        token = auth.token if auth is not None else None
        log.info(
            f"[add_job] name={request.name}, schedule={request.schedule}, notify={request.notify}"
        )
        params = _build_add_params(request)
        raw = await self._port.cron_add(params=params, token=token)
        job = _job_from_dict(raw)
        log.info(f"[add_job] created job: {job.id}")
        return job

    async def update_job(
        self,
        job_id: str,
        request: UpdateJobRequest,
        auth: AuthContext | None = None,
    ) -> CronJob:
        """Update an existing job via `cron.update`.

        Fetches the existing job first when the patch needs payload-kind
        fallback or notify merge (exact same pre-condition as legacy service).
        """
        token = auth.token if auth is not None else None
        log.info(f"[update_job] job_id={job_id}")

        needs_existing = (
            (request.payload is not None and "kind" not in request.payload)
            or request.notify is not None
        )
        existing_job: CronJob | None = None
        if needs_existing:
            existing_job = await self.get_job(job_id, auth=auth)

        patch = _build_update_patch(request, existing_job)
        if not patch:
            log.warning("[update_job] no fields to update")
            raise ValueError("No fields to update")

        log.info(f"[update_job] sending patch: {patch}")
        raw = await self._port.cron_update(job_id=job_id, patch=patch, token=token)
        job = _job_from_dict(raw)
        log.info(f"[update_job] updated job: {job_id}")
        return job

    async def remove_job(
        self, job_id: str, auth: AuthContext | None = None,
    ) -> bool:
        """Remove a job via `cron.remove`."""
        token = auth.token if auth is not None else None
        log.info(f"[remove_job] job_id={job_id}")
        result = await self._port.cron_remove(job_id=job_id, token=token)
        log.info(f"[remove_job] result={result}")
        return result

    async def run_job(
        self,
        job_id: str,
        force: bool = False,
        timeout: int | None = None,
        auth: AuthContext | None = None,
    ) -> dict:
        """Trigger a one-off run of the given job via `cron.run`."""
        token = auth.token if auth is not None else None
        mode = "force" if force else "due"
        log.info(f"[run_job] job_id={job_id}, mode={mode}")
        result = await self._port.cron_run(job_id=job_id, mode=mode, token=token)
        log.info(f"[run_job] ran job: {job_id}")
        return result

    async def get_runs(
        self,
        job_id: str,
        limit: int = 20,
        auth: AuthContext | None = None,
    ) -> list[CronRunRecord]:
        """Return historical run records via `cron.runs`."""
        token = auth.token if auth is not None else None
        log.info(f"[get_runs] job_id={job_id}, limit={limit}")
        raw_runs = await self._port.cron_runs(
            job_id=job_id, limit=limit, token=token
        )
        records = [_run_from_dict(r) for r in raw_runs]
        log.info(f"[get_runs] returning {len(records)} records")
        return records

    async def get_running_jobs(
        self, auth: AuthContext | None = None,
    ) -> list[dict]:
        """List currently-executing jobs.

        Adapter-side composition over `cron_list`: fetches all jobs and
        filters by `state.running_at_ms` — matching legacy
        `OpenClawCronService.get_running_jobs` exactly.
        """
        jobs = await self.list_jobs(auth=auth)
        running: list[dict] = []
        for job in jobs:
            if job.state and "running_at_ms" in job.state:
                running.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "running_at_ms": job.state["running_at_ms"],
                    }
                )
        return running


__all__ = ["OpenClawCronAdapter"]
