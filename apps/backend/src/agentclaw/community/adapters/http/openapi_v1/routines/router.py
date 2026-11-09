"""Authenticated routines over the existing Cron runtime-targeting service."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    BotIdPath,
    Deleted,
    Envelope,
    PageParamsDep,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    created,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.core.cron.errors import CronRelayError
from agentclaw.community.di import Injected

from .params import (
    RoutineActorDep, RuntimeStageQuery, DeviceUuidQuery, OperatorIdQuery, target_parameters,
)
from .converter import _map_routine, failed_targets, map_operation_batch, map_runs, map_trigger
from .relay import call_cron

from .schemas import (
    Routine,
    RoutineTargetSpec,
    RoutinePage,
    RoutineRunPage,
    RoutineOperationBatch,
    RoutineRun,
    RoutineUpdate,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/routines", tags=["routines"], route_class=PublicAPIRoute)

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


@router.get("", response_model=Envelope[RoutinePage])
@envelope_errors
async def list_routines(
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
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
    runtime_stage: RuntimeStageQuery = None,
    device_uuid: DeviceUuidQuery = None,
    operator_id: OperatorIdQuery = None,
    actor: RoutineActorDep = None,
) -> Envelope[RoutinePage]:
    """List a bot's routines (paginated)."""
    # `status` is an openapi query hint with no direct field on `Routine`
    # (which only carries `enabled`). It is currently a no-op: the adapter
    # does not expose a status filter at the list seam, so we return the full
    # set and let the client filter on `enabled`. Wire a server-side filter
    # here only if/when the engine surfaces a status dimension.
    result = await call_cron(factory.list_all_crons, request=request, actor=actor,
        user_id=owner_id,
        nick_name=owner_id,
        bot_id=bot_id,
        **target_parameters(runtime_stage, device_uuid, operator_id, actor=actor, user_id=user_id),
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
    return envelope(RoutinePage(total=len(mapped), items=page_items, failed_targets=failed_targets(result)), request)


@router.post("", status_code=201, response_model=Envelope[Routine])
@envelope_errors
async def create_routine(
    bot_id: BotIdPath,
    body: RoutineTargetSpec,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    actor: RoutineActorDep = None,
) -> Envelope[Routine]:
    """Create a routine on a bot.

    The schedule fires in the routine's timezone, which defaults to
    Asia/Shanghai when omitted. Each firing starts a fresh session and hands
    the bot the command as its user message; `model` overrides which model
    those sessions run on and `timeout_secs` bounds how long one firing runs.
    """
    # Translation to the engine adapter cron body shape: schedule is the raw
    # cron expression STRING (not the nested {kind,expr,tz} dict — the adapter
    # wraps it on read in device_adapter_transport._build_item), timezone
    # defaults to Asia/Shanghai, and timeout_secs defaults in the schema —
    # both to match legacy cron/router.py's create path. `timeout_secs` maps
    # an explicit null back to the same default, so a read-back value copies
    # into a create without tripping validation. `model` flows only when
    # non-empty, exactly like the legacy create: an empty/absent override is
    # left to the bot's default rather than sent as an empty string.
    adapter_body = {
        "name": body.name,
        "schedule": body.trigger.cron,
        "command": body.command,
        "timezone": body.timezone or "Asia/Shanghai",
        "enabled": body.enabled,
        "timeout_secs": body.timeout_secs if body.timeout_secs is not None else 86400,
    }
    if body.model:
        adapter_body["model"] = body.model
    result = await call_cron(factory.create_cron, request=request, actor=actor,
        bot_id=bot_id,
        user_id=owner_id,
        nick_name=owner_id,
        body=adapter_body,
        **target_parameters(getattr(body, "runtime_stage", None), getattr(body, "device_uuid", None),
                            getattr(body, "operator_id", None), actor=actor, user_id=user_id),
    )
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="cron service returned no data")
    return created(_map_routine(data), request)


@router.get("/{routine_id}", response_model=Envelope[Routine])
@envelope_errors
async def get_routine(
    routine_id: RoutineIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    runtime_stage: RuntimeStageQuery = "draft",
    device_uuid: DeviceUuidQuery = None,
    operator_id: OperatorIdQuery = None,
    actor: RoutineActorDep = None,
) -> Envelope[Routine]:
    """Get a routine.

    The bot is named by the path — a routine id alone does not identify the
    bot that holds it. Note the returned bot_id may be empty on this read;
    keep the one you addressed with.
    """
    # C3: a routine id does not reverse-map to a bot, so the bot has to be
    # named. It is on the path now, ahead of the routine. The owner is the
    # addressed one (OwnerIdDep); the cron relay still attributes the job to
    # that owner's identity. Missing/non-dict data collapses to 404.
    result = await call_cron(factory.get_cron_detail, request=request, actor=actor,
        bot_id=bot_id, user_id=owner_id, nick_name=owner_id, task_id=routine_id,
        **target_parameters(runtime_stage, device_uuid, operator_id, actor=actor, user_id=user_id, default_stage="draft"),
    )
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Routine not found")
    return envelope(_map_routine(data), request)


@router.patch("/{routine_id}", response_model=Envelope[Routine | RoutineOperationBatch])
@envelope_errors
async def update_routine(
    routine_id: RoutineIdPath,
    body: RoutineUpdate,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    runtime_stage: RuntimeStageQuery = "draft",
    device_uuid: DeviceUuidQuery = None,
    operator_id: OperatorIdQuery = None,
    actor: RoutineActorDep = None,
) -> Envelope[Routine]:
    """Update a routine. Omitted fields are left unchanged.

    When sending a new trigger, send the timezone with it — a trigger update
    without one resets the schedule's zone to the default.
    """
    # C3: the bot is named on the path (see get_routine). Partial update: only
    # set fields flow to the adapter. trigger.cron becomes a raw schedule
    # STRING (not a {kind,expr,tz} dict — the adapter wraps it on read; Task 3
    # contract). Missing/non-dict data on the response collapses to 404.
    update_body: dict = {}
    if body.name is not None:
        update_body["name"] = body.name
    if body.command is not None:
        update_body["command"] = body.command
    if body.timezone is not None:
        update_body["timezone"] = body.timezone
    if body.enabled is not None:
        update_body["enabled"] = body.enabled
    # Truthy like the create guard, not ``is not None``: an empty string is
    # not an override the caller can mean, and the engine would store it in
    # the payload only for every read to fold back to null. Symmetric with
    # create keeps the two paths from drifting apart; null stays "keep" like
    # every other partial-update field here.
    if body.model:
        update_body["model"] = body.model
    if body.timeout_secs is not None:
        update_body["timeout_secs"] = body.timeout_secs
    if body.trigger is not None:
        update_body["schedule"] = body.trigger.cron
    result = await call_cron(factory.update_cron, request=request, actor=actor,
        bot_id=bot_id,
        user_id=owner_id,
        nick_name=owner_id,
        task_id=routine_id,
        body=update_body,
        **target_parameters(runtime_stage, device_uuid, operator_id, actor=actor, user_id=user_id, default_stage="draft"),
    )
    if isinstance(result.get("data"), dict) and "results" in result["data"]:
        return envelope(map_operation_batch(result, routine_id, run=False), request)
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(status_code=404, detail="Routine not found")
    return envelope(_map_routine(data), request)


@router.delete("/{routine_id}", response_model=Envelope[Deleted])
@envelope_errors
async def delete_routine(
    routine_id: RoutineIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    runtime_stage: RuntimeStageQuery = "draft",
    device_uuid: DeviceUuidQuery = None,
    operator_id: OperatorIdQuery = None,
    actor: RoutineActorDep = None,
) -> Envelope[Deleted]:
    """Delete a routine.

    A failure is never reported as a successful delete: an unknown routine
    answers 404 and a timeout answers 502.
    """
    # C3: the bot is named on the path, ahead of the routine.
    # Without explicit targeting, delete_cron only accepts draft; a published-stage delete
    # raises CronRelayError (error_code 403), mapped here (the code is dynamic
    # per-raise, so not in the static ENVELOPE_ERRORS map). The engine result
    # distinguishes timeout-vs-missing only via its error text today; a
    # structured engine error_code would make that precise (follow-up).
    try:
        result = await call_cron(factory.delete_cron, request=request, actor=actor, check_success=False,
            bot_id=bot_id, user_id=owner_id, nick_name=owner_id, task_id=routine_id,
        **target_parameters(runtime_stage, device_uuid, operator_id, actor=actor, user_id=user_id, default_stage="draft"),
        )
    except CronRelayError as e:
        # published-stage delete rejected — CronRelayError carries the engine's
        # error_code (dynamic, so not in the static ENVELOPE_ERRORS map).
        raise HTTPException(
            status_code=getattr(e, "error_code", None) or 500, detail="Cron relay service error"
        ) from None
    success = bool(result.get("success")) if isinstance(result, dict) else False
    if not success:
        error = str(result.get("error", "") or "") if isinstance(result, dict) else ""
        if "timeout" in error.lower() or "timed out" in error.lower():
            raise HTTPException(status_code=502, detail="Routine delete timed out")
        raise HTTPException(
            status_code=404, detail="Routine not found or delete failed"
        )
    return envelope(Deleted(deleted=True), request)


@router.post("/{routine_id}/run", response_model=Envelope[RoutineRun | RoutineOperationBatch])
@envelope_errors
async def run_routine(
    routine_id: RoutineIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    runtime_stage: RuntimeStageQuery = "draft",
    device_uuid: DeviceUuidQuery = None,
    operator_id: OperatorIdQuery = None,
    actor: RoutineActorDep = None,
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
    result = await call_cron(factory.run_cron, request=request, actor=actor,
        bot_id=bot_id,
        user_id=owner_id,
        nick_name=owner_id,
        task_id=routine_id,
        force=True,
        **target_parameters(runtime_stage, device_uuid, operator_id, actor=actor, user_id=user_id, default_stage="draft"),
    )
    if not isinstance(result, dict) or not result.get("success", False):
        # Routed via the ``@envelope_errors`` decorator to the ENVELOPE_ERRORS
        # entry for ``CronRelayError`` (502, fixed public "Cron relay service
        # error"), instead of leaking the adapter detail or hitting an
        # app-level 500 with a vague message.
        raise CronRelayError("routine trigger failed", error_code=502)

    data = result.get("data")
    if not isinstance(data, dict):
        raise CronRelayError("routine trigger returned no data", error_code=502)

    if "results" in data:
        return envelope(map_operation_batch(result, routine_id, run=True), request)
    return envelope(map_trigger(data, routine_id), request)


@router.get(
    "/{routine_id}/runs",
    response_model=Envelope[RoutineRunPage],
)
@envelope_errors
async def list_routine_runs(
    routine_id: RoutineIdPath,
    page: PageParamsDep,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    bot_id: BotIdPath,
    request: Request,
    factory: CronRelayServiceProtocol = Injected(CronRelayServiceProtocol),
    runtime_stage: RuntimeStageQuery = None,
    device_uuid: DeviceUuidQuery = None,
    operator_id: OperatorIdQuery = None,
    actor: RoutineActorDep = None,
) -> Envelope[RoutineRunPage]:
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
    result = await call_cron(factory.get_cron_runs, request=request, actor=actor,
        bot_id=bot_id, user_id=owner_id, nick_name=owner_id, task_id=routine_id,
        **target_parameters(runtime_stage, device_uuid, operator_id, actor=actor, user_id=user_id, default_stage="draft"),
    )
    mapped = map_runs(result, routine_id)
    start = (page.page - 1) * page.page_size
    return envelope(RoutineRunPage(total=len(mapped), items=mapped[start:start + page.page_size],
                                   failed_targets=failed_targets(result)), request)
