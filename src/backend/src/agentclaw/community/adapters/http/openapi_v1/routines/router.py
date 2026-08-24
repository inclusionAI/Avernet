"""Routines group — ``/openapi/v1/bots/{bot_id}/routines`` (definition only).

Scheduled/triggered agent tasks (the former "cron"), with a stable
gateway-owned schema and a nested trigger. Handlers are stubs; every route
requires an authenticated user principal.
"""

from __future__ import annotations

from datetime import datetime, timezone as _tz
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    Page,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.cron.errors import (
    CronApiTimeoutError,
    CronRelayError,
)
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    RUNTIME_STAGE_DRAFT,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

from .schemas import Routine, RoutineSpec, RoutineRun, RoutineUpdate, ScheduleTrigger
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/routines", tags=["routines"], route_class=PublicAPIRoute)

logger = get_logger()

#: The path parameter naming the routine an operation addresses.
RoutineIdPath = Annotated[
    str,
    Path(
        description="The routine's id, exactly as returned on create or in "
        "the listing — an opaque string; treat it as a token."
    ),
]

#: Routines are addressed (bot_id, routine_id) together: the routine id alone
#: does not say which bot's engine holds it. Both are path segments, so the
#: address names the pair rather than half of it — which is also what lets the
#: grant check run as a dependency for every operation in the group, create
#: included.


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
    owner_id: UserIdDep,
    bot_id: BotIdPath,
    request: Request,
    status: Annotated[
        str | None,
        Query(
            description="Reserved; currently ignored — the full set is "
            "returned. Filter client-side on `enabled`."
        ),
    ] = None,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Page[Routine]]:
    """List a bot's routines (paginated)."""
    # `status` is an openapi query hint with no direct field on `Routine`
    # (which only carries `enabled`). It is currently a no-op: the adapter
    # does not expose a status filter at the list seam, so we return the full
    # set and let the client filter on `enabled`. Wire a server-side filter
    # here only if/when the engine surfaces a status dimension.
    user_id = owner_id
    nick_name = owner_id
    # Draft only, like every other route in this group: the public surface
    # operates a bot's pre-publication workspace, so a service bot's published
    # verify/online runtimes are neither listed nor queried here.
    result = await factory.list_all_crons(
        user_id=user_id,
        nick_name=nick_name,
        bot_id=bot_id,
        runtime_stage=RUNTIME_STAGE_DRAFT,
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
    bot_id: BotIdPath,
    body: RoutineSpec,
    owner_id: UserIdDep,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Routine]:
    """Create a routine on a bot.

    The schedule fires in the routine's timezone, which defaults to
    Asia/Shanghai when omitted. Each firing starts a fresh session and hands
    the bot the command as its user message.
    """
    # Translation to the engine adapter cron body shape: schedule is the raw
    # cron expression STRING (not the nested {kind,expr,tz} dict — the adapter
    # wraps it on read in device_adapter_transport._build_item), and timezone
    # defaults to Asia/Shanghai to match legacy cron/router.py's create path.
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
    routine_id: RoutineIdPath,
    owner_id: UserIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Routine]:
    """Get a routine.

    The bot is named by the path — a routine id alone does not identify the
    bot that holds it. Note the returned bot_id may be empty on this read;
    keep the one you addressed with.
    """
    # C3: a routine id does not reverse-map to a bot, so the bot has to be
    # named. It is on the path now, ahead of the routine. Owner identity comes from the
    # authenticated principal via UserIdDep. Missing/non-dict data collapses
    # to 404.
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
    routine_id: RoutineIdPath,
    body: RoutineUpdate,
    owner_id: UserIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Routine]:
    """Update a routine. Omitted fields are left unchanged.

    When sending a new trigger, send the timezone with it — a trigger update
    without one resets the schedule's zone to the default.
    """
    # C3: the bot is named on the path (see get_routine). Partial update: only
    # set fields flow to the adapter. trigger.cron becomes a raw schedule
    # STRING (not a {kind,expr,tz} dict — the adapter wraps it on read; Task 3
    # contract). Missing/non-dict data on the response collapses to 404.
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
    routine_id: RoutineIdPath,
    owner_id: UserIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Deleted]:
    """Delete a routine.

    A failure is never reported as a successful delete: an unknown routine
    answers 404 and a timeout answers 502.
    """
    # C3: the bot is named on the path, ahead of the routine.
    # delete_cron only operates on the draft stage; a published-stage delete
    # raises CronRelayError (error_code 403), mapped here (the code is dynamic
    # per-raise, so not in the static ENVELOPE_ERRORS map). The engine result
    # distinguishes timeout-vs-missing only via its error text today; a
    # structured engine error_code would make that precise (follow-up).
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
    routine_id: RoutineIdPath,
    owner_id: UserIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[RoutineRun]:
    """Trigger a routine now.

    The response describes the trigger attempt, not a finished run: status is
    'completed' (the engine acknowledged the trigger), 'failed' (it declined,
    with a reason) or 'unknown', and both timestamps are null. Read the runs
    listing for actual execution results and timings.
    """
    # "Run now" is unconditional.  The runtime's default ``force=False`` means
    # "run only when due" on OpenClaw; using that default here makes a manual
    # click at any other time a no-op.  Worse, OpenClaw still returns
    # ``ran=<routine id>, status=not_due`` and ``bool(ran)`` is true, so the old
    # mapping reported that no-op as ``completed``.  Force the trigger and read
    # the runtime status rather than treating its opaque routine id as a bool.
    user_id = owner_id
    nick_name = owner_id
    try:
        result = await factory.run_cron(
            bot_id=bot_id,
            user_id=user_id,
            nick_name=nick_name,
            task_id=routine_id,
            force=True,
        )
    except (CronRelayError, CronApiTimeoutError):
        # Let the ENVELOPE_ERRORS mapping handle these; the fixed public message
        # is applied by the app-level envelope handler.
        raise
    except Exception as exc:
        # ValueError ("Bot has no device binding") and other non-ENVELOPE_ERRORS
        # exceptions are translated here so the public response is the fixed
        # "Cron relay service error" envelope (502), not a vague 500 with an
        # internal identifier like ``bot_id`` leaking into it.
        logger.warning("[run_routine] routine trigger relay failed: %s", exc)
        raise CronRelayError("routine trigger failed", error_code=502) from exc
    if not isinstance(result, dict) or not result.get("success", False):
        # Routed via the ``@envelope_errors`` decorator to the ENVELOPE_ERRORS
        # entry for ``CronRelayError`` (502, fixed public "Cron relay service
        # error"), instead of leaking the adapter detail or hitting an
        # app-level 500 with a vague message.
        raise CronRelayError("routine trigger failed", error_code=502)

    data = result.get("data")
    if not isinstance(data, dict):
        raise CronRelayError("routine trigger returned no data", error_code=502)

    runtime_status = str(data.get("status", "") or "").lower()
    reason = str(data.get("reason", "") or "")
    ran = data.get("ran")
    if runtime_status in {"dispatched", "started", "running", "success", "completed"}:
        status = "completed"
    elif runtime_status in {"already_running", "not_due", "skipped", "failed", "error"}:
        status = "failed"
    elif isinstance(ran, bool):
        status = "completed" if ran else ("failed" if reason else "unknown")
    elif ran:
        # Claude Code currently acknowledges a successful trigger as
        # ``{"ran": <routine id>}`` without a separate status.
        status = "completed"
    elif reason:
        status = "failed"
    else:
        status = "unknown"

    upstream_run_id = data.get("run_id") or data.get("runId")
    run_id = (
        str(upstream_run_id)
        if upstream_run_id
        else (f"{routine_id}-{datetime.now(_tz.utc).isoformat()}")
    )
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
    routine_id: RoutineIdPath,
    page: PageParamsDep,
    owner_id: UserIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
) -> Envelope[Page[RoutineRun]]:
    """List a routine's execution history, most recent first.

    The engine keeps a bounded history per routine, so deep pages come back
    empty by construction.
    """
    # The bot is named on the path (C3). get_cron_runs returns
    # {"success":..,"data":{"runs":[{job_id, started_at_ms, finished_at_ms,
    # status, ...}]}} in draft mode (_forward_single_stage_request
    # passthrough; _decorate_single_result only adds bot_metadata fields to
    # data, leaving runs intact). Each entry maps via _map_run; pagination is
    # client-side over the fetched set.
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
