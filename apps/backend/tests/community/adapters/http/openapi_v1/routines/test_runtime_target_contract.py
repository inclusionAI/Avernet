"""OpenAPI targets preserve the service's method-specific routing contract."""

from importlib import import_module
from fastapi import HTTPException
from agentclaw.community.adapters.http.openapi_v1.routines.schemas import RoutineTargetSpec, RoutineUpdate
from agentclaw.community.adapters.http.openapi_v1.routines.params import RoutineActor
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.contracts import PageParams
from agentclaw.community.adapters.http.openapi_v1.routines.router import list_routines, get_routine


routes = import_module("agentclaw.community.adapters.http.openapi_v1.routines.router")

def request():
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_list_omitted_stage_passes_actor_and_aggregates():
    svc = AsyncMock()
    svc.list_all_crons.return_value = {"success": True, "data": []}
    await list_routines(page=PageParams(), user_id="actor", owner_id="owner",
                        bot_id="bot", request=request(), factory=svc)
    assert svc.list_all_crons.call_args.kwargs["runtime_stage"] is None
    assert svc.list_all_crons.call_args.kwargs["authenticated_user_id"] == "actor"


@pytest.mark.asyncio
async def test_detail_forwards_full_target_and_trusted_response_context():
    svc = AsyncMock()
    svc.get_cron_detail.return_value = {"success": True, "data": {
        "id": "job", "device_uuid": "resolved", "operator_id": "resolved-user",
        "bot": {"bot_id": "bot", "owner_id": "owner", "runtime_stage": "online"},
    }}
    result = await get_routine(routine_id="job", user_id="actor", owner_id="owner",
                               bot_id="bot", request=request(), factory=svc,
                               runtime_stage="online", device_uuid="selected", operator_id="caller")
    assert svc.get_cron_detail.call_args.kwargs["device_uuid"] == "selected"
    assert svc.get_cron_detail.call_args.kwargs["authenticated_user_id"] == "actor"
    assert result.data.device_uuid == "resolved"
    assert result.data.operator_id == "resolved-user"
    assert result.data.runtime_stage == "online"
    assert result.data.bot_id == "bot"


@pytest.mark.asyncio
async def test_list_preserves_empty_success_partial_failure():
    svc = AsyncMock()
    svc.list_all_crons.return_value = {"success": True, "data": [], "failed_targets": [
        {"bot_id": "bot", "runtime_stage": "online", "device_uuid": "unavailable",
         "reason": "cron_api_failed", "message": "private provider detail"},
    ]}
    result = await list_routines(page=PageParams(), user_id="actor", owner_id="owner",
                                 bot_id="bot", request=request(), factory=svc)
    assert result.data.total == 0
    assert result.data.failed_targets[0].device_uuid == "unavailable"
    assert result.data.failed_targets[0].reason == "cron_api_failed"
    assert "private provider" not in result.model_dump_json()




_OPERATIONS = [
    ("list_routines", "list_all_crons", None),
    ("create_routine", "create_cron", None),
    ("get_routine", "get_cron_detail", "draft"),
    ("update_routine", "update_cron", "draft"),
    ("delete_routine", "delete_cron", "draft"),
    ("run_routine", "run_cron", "draft"),
    ("list_routine_runs", "get_cron_runs", "draft"),
]


async def invoke(handler_name, svc, target=None, actor=None):
    kwargs = dict(user_id="acting", owner_id="owner", bot_id="bot", request=request(),
                  factory=svc, actor=actor)
    target = target or {}
    if handler_name == "create_routine":
        kwargs["body"] = RoutineTargetSpec(name="daily", trigger={"cron": "0 9 * * *"},
                                            command="full business command", **target)
    else:
        kwargs.update(target)
    if handler_name not in {"list_routines", "create_routine"}:
        kwargs["routine_id"] = "job"
    if handler_name in {"list_routines", "list_routine_runs"}:
        kwargs["page"] = PageParams()
    if handler_name == "update_routine":
        kwargs["body"] = RoutineUpdate(enabled=False)
    return await getattr(routes, handler_name)(**kwargs)


def result_service(method, result=None):
    svc = AsyncMock()
    getattr(svc, method).return_value = result if result is not None else {
        "success": True, "data": {"id": "job", "status": "dispatched", "runs": []},
    }
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,method,default", _OPERATIONS)
@pytest.mark.parametrize("target", [{}, {"runtime_stage": "online"},
    {"runtime_stage": "online", "device_uuid": "instance", "operator_id": "selected"}])
async def test_seven_operations_preserve_raw_selectors_owner_and_verified_actor(handler, method, default, target):
    svc = result_service(method)
    await invoke(handler, svc, target, RoutineActor("verified", "user"))
    args = getattr(svc, method).call_args.kwargs
    assert args["user_id"] == "owner"
    assert args["authenticated_user_id"] == "verified"
    assert args["runtime_stage"] == target.get("runtime_stage", default)
    assert args["device_uuid"] == target.get("device_uuid")
    assert args["operator_id"] == target.get("operator_id")
    if "body" in args:
        assert not {"runtime_stage", "device_uuid", "operator_id"} & args["body"].keys()
    if handler == "run_routine":
        assert args["force"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,method,default", [row for row in _OPERATIONS if row[0] in {"list_routines", "create_routine", "list_routine_runs"}])
async def test_selectors_without_stage_fail_before_service(handler, method, default):
    svc = result_service(method)
    with pytest.raises(HTTPException) as exc:
        await invoke(handler, svc, {"device_uuid": "instance"})
    assert exc.value.status_code == 400
    getattr(svc, method).assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,method", [("update_routine", "update_cron"), ("run_routine", "run_cron")])
async def test_broadcast_success_maps_each_instance(handler, method):
    rows = [{"bot_id": "bot", "owner_id": "owner", "runtime_stage": "online",
             "device_uuid": uuid, "success": True,
             "data": {"id": "job", "ran": True, "status": "dispatched"}}
            for uuid in ["a", "b"]]
    svc = result_service(method, {"success": True, "data": {"results": rows, "succeeded": 2, "failed": 0}})
    result = await invoke(handler, svc, {"runtime_stage": "online"})
    assert result.data.succeeded == 2
    assert [row.device_uuid for row in result.data.results] == ["a", "b"]
    for row in result.data.results:
        if handler == "run_routine":
            assert row.run.status == "completed"
            assert row.run.started_at is None
        else:
            assert row.routine.routine_id == "job"


@pytest.mark.asyncio
async def test_history_flattens_parent_targets_sorts_and_keeps_duplicate_ids():
    svc = result_service("get_cron_runs", {"success": True, "data": {"results": [
        {"success": True, "bot_id": "bot", "device_uuid": "a", "runtime_stage": "online",
         "data": {"runs": [{"job_id": "same", "started_at_ms": 1000}]}},
        {"success": True, "bot_id": "bot", "device_uuid": "b", "runtime_stage": "online",
         "data": {"runs": [{"job_id": "same", "started_at_ms": 2000}]}},
    ]}})
    result = await invoke("list_routine_runs", svc, {"runtime_stage": "online"})
    assert result.data.total == 2
    assert [row.device_uuid for row in result.data.items] == ["b", "a"]
    assert [row.run_id for row in result.data.items] == ["same", "same"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,method", [("get_routine", "get_cron_detail"), ("run_routine", "run_cron")])
@pytest.mark.parametrize("code", [404, 504])
async def test_target_error_status_is_preserved(handler, method, code):
    from agentclaw.community.core.cron.errors import CronRelayError
    svc = result_service(method)
    getattr(svc, method).side_effect = CronRelayError("private provider diagnostic", code)
    with pytest.raises(HTTPException) as exc:
        await invoke(handler, svc, {"runtime_stage": "online", "device_uuid": "a"})
    assert exc.value.status_code == code
    assert "private" not in exc.value.detail


def test_parent_run_target_cannot_be_overridden_by_engine_row():
    from agentclaw.community.adapters.http.openapi_v1.routines.converter import _map_run, map_operation_batch
    context = {"bot_id": "trusted", "device_uuid": "trusted-device", "operator_id": "trusted-user"}
    forged = {"job_id": "job", "bot_id": "forged", "device_uuid": "forged", "operator_id": "forged"}
    row = _map_run(forged, "job", context)
    assert row.bot_id == "trusted"
    assert row.device_uuid == "trusted-device"
    batch = map_operation_batch({"data": {"results": [{**context, "success": True, "data": {"id": "job", **forged}}],
                                         "succeeded": 1, "failed": 0}}, "job", run=False)
    assert batch.results[0].routine.operator_id == "trusted-user"


@pytest.mark.asyncio
async def test_actor_dependency_uses_verified_human_despite_acting_user_bypass():
    from agentclaw.community.adapters.http.openapi_v1.routines.params import resolve_routine_actor
    from agentclaw.community.adapters.http.openapi_v1.admission import ActingCaller
    actor = await resolve_routine_actor(ActingCaller(user_id="bypass", app_id=None), {"user_id": "real-human"})
    assert actor.user_id == "real-human"
