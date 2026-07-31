"""Routines group — ``/openapi/v1/routines`` (definition only).

Scheduled/triggered agent tasks (the former "cron"), with a stable
gateway-owned schema and a nested trigger. Handlers are stubs; every route
requires an authenticated user principal.
"""

from __future__ import annotations

from datetime import datetime, timezone as _tz
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    Deleted,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.di import Injected

from .schemas import Routine, RoutineCreate, RoutineRun, RoutineUpdate, ScheduleTrigger

router = APIRouter(prefix="/openapi/v1/bots/routines", tags=["routines"])

PrincipalDep = Annotated[Principal, Depends(require_principal)]


def _ms_to_iso(ms: Any) -> str:
    """Convert epoch milliseconds to an ISO 8601 string; ``""`` on falsy/invalid."""
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=_tz.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _map_routine(data: dict) -> Routine:
    """Map an engine adapter cron dict → openapi Routine schema.

    Field source: ``plugins/local/device_adapter_transport.py`` `_build_item`
    (``id`` / ``name`` / ``enabled`` / ``schedule{expr,tz}`` /
    ``payload.message`` / ``created_at_ms`` / ``updated_at_ms``) plus the
    top-level ``bot_id`` injected by ``_decorate_runtime_item`` in
    ``cron_runtime_targets.py``.
    """
    sched = data.get("schedule") or {}
    payload = data.get("payload") or {}
    return Routine(
        routine_id=str(data.get("id", "")),
        bot_id=str(data.get("bot_id", "")),
        name=str(data.get("name", "") or ""),
        trigger=ScheduleTrigger(type="schedule", cron=str(sched.get("expr", ""))),
        command=str(payload.get("message", "") or ""),
        enabled=bool(data.get("enabled", False)),
        timezone=sched.get("tz") or None,
        gmt_create=_ms_to_iso(data.get("created_at_ms")),
        gmt_modified=_ms_to_iso(data.get("updated_at_ms")),
    )


def _map_run(data: dict, routine_id: str) -> RoutineRun:
    """Map an engine adapter run dict → openapi RoutineRun.

    Field source: adapter ``runs[]`` entries from
    ``plugins/local/device_adapter_transport.py`` carry ``job_id`` +
    ``started_at_ms`` / ``finished_at_ms`` / ``status`` / ``error`` /
    ``duration_ms``. ``RoutineRun`` has no duration field, so we carry
    status + timestamps and synthesize ``run_id`` from ``job_id`` (test
    stubs sometimes use ``run_id`` — accept both). ``_ms_to_iso`` returns
    ``""`` on falsy; coerce to ``None`` to match the ``str | None`` schema.
    """
    job_id = data.get("job_id") or data.get("run_id") or data.get("id") or ""
    started = _ms_to_iso(data.get("started_at_ms")) or None
    finished = _ms_to_iso(data.get("finished_at_ms")) or None
    return RoutineRun(
        run_id=str(job_id),
        routine_id=routine_id,
        status=str(data.get("status", "") or ""),
        started_at=started,
        finished_at=finished,
    )


@router.get("", response_model=Envelope[Page[Routine]])
@envelope_errors
async def list_routines(
    page: PageParamsDep,
    principal: PrincipalDep,
    bot_id: str,
    request: Request,
    status: str | None = None,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Page[Routine]]:
    """List routines (filter + paginate).

    ``status`` is an openapi query hint with no direct field on ``Routine``
    (which only carries ``enabled``). It is currently a no-op: the adapter
    does not expose a status filter at the list seam, so we return the full
    set and let the client filter on ``enabled``. Wire a server-side filter
    here only if/when the engine surfaces a status dimension.
    """
    owner_id = caller_owner_id(principal)
    user_id = owner_id
    nick_name = owner_id
    result = await factory.list_all_crons(
        user_id=user_id, nick_name=nick_name, bot_id=bot_id
    )
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, list):
        items_list = data
    elif isinstance(data, dict):
        items_list = data.get("items", [])
    else:
        items_list = []
    mapped = [_map_routine(d) for d in items_list if isinstance(d, dict)]
    start = (page.page - 1) * page.page_size
    end = start + page.page_size
    page_items = mapped[start:end]
    return page_envelope(len(mapped), page_items, request)


@router.post("", status_code=201, response_model=Envelope[Routine])
@envelope_errors
async def create_routine(
    body: RoutineCreate,
    principal: PrincipalDep,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Routine]:
    """Create a routine.

    Translates the openapi ``RoutineCreate`` body into the engine adapter
    cron body shape: ``schedule`` is the raw cron expression STRING (not
    the nested ``{kind,expr,tz}`` dict — the adapter wraps it on read in
    ``device_adapter_transport._build_item``), and ``timezone`` defaults
    to ``Asia/Shanghai`` to match legacy ``cron/router.py``'s create path.
    """
    bot_id = body.bot_id
    owner_id = caller_owner_id(principal)
    user_id = owner_id
    nick_name = owner_id
    adapter_body = {
        "name": body.name,
        "schedule": body.trigger.cron,
        "command": body.command,
        "timezone": body.timezone or "Asia/Shanghai",
        "enabled": body.enabled,
        "timeout_secs": 86400,
    }
    result = await factory.create_cron(
        bot_id=bot_id,
        user_id=user_id,
        nick_name=nick_name,
        body=adapter_body,
    )
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="cron service returned no data")
    return created(_map_routine(data), request)


@router.get("/{routine_id}", response_model=Envelope[Routine])
@envelope_errors
async def get_routine(
    routine_id: str,
    principal: PrincipalDep,
    bot_id: str,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Routine]:
    """Get a routine.

    C3: the path carries only ``routine_id`` (no routine table to
    reverse-map to a bot), so ``bot_id`` is a required query. Owner
    identity comes from the authenticated principal via
    ``caller_owner_id``. Missing/non-dict ``data`` collapses to 404.
    """
    owner_id = caller_owner_id(principal)
    user_id = owner_id
    nick_name = owner_id
    result = await factory.get_cron_detail(
        bot_id=bot_id, user_id=user_id, nick_name=nick_name, task_id=routine_id
    )
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Routine not found")
    return envelope(_map_routine(data), request)


@router.patch("/{routine_id}", response_model=Envelope[Routine])
@envelope_errors
async def update_routine(
    routine_id: str,
    body: RoutineUpdate,
    principal: PrincipalDep,
    bot_id: str,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Routine]:
    """Update a routine (partial).

    C3: ``bot_id`` is a required query (see ``get_routine``). Partial
    update: only set fields flow to the adapter. ``trigger.cron`` becomes
    a raw ``schedule`` STRING (not a ``{kind,expr,tz}`` dict — the adapter
    wraps it on read; Task 3 contract). Missing/non-dict ``data`` on the
    response collapses to 404.
    """
    owner_id = caller_owner_id(principal)
    user_id = owner_id
    nick_name = owner_id
    update_body: dict = {}
    if body.name is not None:
        update_body["name"] = body.name
    if body.command is not None:
        update_body["command"] = body.command
    if body.timezone is not None:
        update_body["timezone"] = body.timezone
    if body.enabled is not None:
        update_body["enabled"] = body.enabled
    if body.trigger is not None:
        update_body["schedule"] = body.trigger.cron
    result = await factory.update_cron(
        bot_id=bot_id,
        user_id=user_id,
        nick_name=nick_name,
        task_id=routine_id,
        body=update_body,
    )
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Routine not found")
    return envelope(_map_routine(data), request)


@router.delete("/{routine_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_routine(
    routine_id: str,
    principal: PrincipalDep,
    bot_id: str,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Deleted]:
    """Delete a routine.

    C3: ``bot_id`` is a required query (path carries only ``routine_id``).
    ``delete_cron`` only operates on the draft stage; a published-stage delete
    raises ``CronRelayError`` (error_code 403), mapped here (the code is dynamic
    per-raise, so not in the static ``ENVELOPE_ERRORS`` map). A failed draft
    delete is NOT surfaced as ``200 {deleted: false}`` — a missing routine_id
    maps to 404 and a relay timeout to 502. The engine result distinguishes the
    two only via its ``error`` text today; a structured engine ``error_code``
    would make timeout-vs-missing precise (follow-up).
    """
    owner_id = caller_owner_id(principal)
    user_id = owner_id
    nick_name = owner_id
    try:
        result = await factory.delete_cron(
            bot_id=bot_id, user_id=user_id, nick_name=nick_name, task_id=routine_id
        )
    except CronRelayError as e:
        # published-stage delete rejected — CronRelayError carries the engine's
        # error_code (dynamic, so not in the static ENVELOPE_ERRORS map).
        raise HTTPException(
            status_code=getattr(e, "error_code", None) or 500, detail=str(e)
        ) from e
    success = bool(result.get("success")) if isinstance(result, dict) else False
    if not success:
        error = str(result.get("error", "") or "") if isinstance(result, dict) else ""
        if "timeout" in error.lower() or "timed out" in error.lower():
            raise HTTPException(status_code=502, detail="Routine delete timed out")
        raise HTTPException(
            status_code=404, detail="Routine not found or delete failed"
        )
    return envelope(Deleted(deleted=True), request)


@router.post("/{routine_id}/run", response_model=Envelope[RoutineRun])
@envelope_errors
async def run_routine(
    routine_id: str,
    principal: PrincipalDep,
    bot_id: str,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[RoutineRun]:
    """Run a routine now.

    C3: ``bot_id`` is a required query. ``run_cron`` returns
    ``{"success":..,"data":{"ok":..,"ran":..,"reason":..}}`` — no run_id, no
    timestamps. We synthesize ``run_id`` from ``routine_id`` + the handler's
    UTC trigger timestamp (uniqueness is good enough for an immediate-trigger
    echo; the engine has no run_id to return). ``status`` is derived from
    ``ran``/``reason``: completed | failed | unknown. ``started_at`` /
    ``finished_at`` are None because the adapter doesn't surface them on the
    run-trigger seam (use ``GET /{routine_id}/runs`` for actual timestamps).
    """
    owner_id = caller_owner_id(principal)
    user_id = owner_id
    nick_name = owner_id
    result = await factory.run_cron(
        bot_id=bot_id, user_id=user_id, nick_name=nick_name, task_id=routine_id
    )
    data = result.get("data") if isinstance(result, dict) else None
    ran = bool(data.get("ran")) if isinstance(data, dict) else False
    reason = str(data.get("reason", "") or "") if isinstance(data, dict) else ""
    if ran:
        status = "completed"
    elif reason:
        status = "failed"
    else:
        status = "unknown"
    run_id = f"{routine_id}-{datetime.now(_tz.utc).isoformat()}"
    return envelope(
        RoutineRun(
            run_id=run_id,
            routine_id=routine_id,
            status=status,
            started_at=None,
            finished_at=None,
        ),
        request,
    )


@router.get(
    "/{routine_id}/runs",
    response_model=Envelope[Page[RoutineRun]],
)
@envelope_errors
async def list_routine_runs(
    routine_id: str,
    page: PageParamsDep,
    principal: PrincipalDep,
    bot_id: str,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Page[RoutineRun]]:
    """List a routine's execution history.

    C3: ``bot_id`` is a required query. ``get_cron_runs`` returns
    ``{"success":..,"data":{"runs":[{job_id, started_at_ms, finished_at_ms,
    status, ...}]}}`` in draft mode (``_forward_single_stage_request``
    passthrough; ``_decorate_single_result`` only adds bot_metadata fields to
    ``data``, leaving ``runs`` intact). We map each entry via ``_map_run``
    and paginate client-side.
    """
    owner_id = caller_owner_id(principal)
    user_id = owner_id
    nick_name = owner_id
    result = await factory.get_cron_runs(
        bot_id=bot_id, user_id=user_id, nick_name=nick_name, task_id=routine_id
    )
    runs: list[dict] = []
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict):
            raw_runs = data.get("runs") or []
        elif isinstance(data, list):
            # Defensive: engine ever returns data as a bare list of runs.
            raw_runs = data
        else:
            raw_runs = result.get("runs") or []
        runs = [r for r in raw_runs if isinstance(r, dict)]
    mapped = [_map_run(r, routine_id) for r in runs]
    start = (page.page - 1) * page.page_size
    end = start + page.page_size
    page_items = mapped[start:end]
    return page_envelope(len(mapped), page_items, request)
