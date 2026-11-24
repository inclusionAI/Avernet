"""Registered dynamic Swarm operation cases; no worker or product policy is faked."""

from datetime import datetime, timedelta, timezone

from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmMethodOpsServiceProtocol,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from tests.community.factories.context_collab_methods import (
    BASE,
    BINDING,
    HEADERS,
    OWNER,
    seed_method,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PLAN = "plan_http"
_STAGE = "stage_http"
_PROFILE = "execution_http"
_ALLOCATION = "allocation_http"
_PROFILE_BODY = {
    "mode": "A2A_AGENT",
    "target_ref": "participant://worker",
    "supported_work_kinds": ["EXECUTE"],
    "input_contract_ref": "contract://input",
    "execution_profile_id": _PROFILE,
}


def _register(method, suffix, operation, state, happy):
    params = {"binding_id": BINDING, "plan_id": _PLAN, "stage_id": _STAGE}
    body = {"reason": "HTTP Swarm transition"}
    before = {}

    def seed(world):
        collet_id = seed_method(world, "Swarm")
        params["collab_context_id"] = collet_id
        task = world.get(ContextServiceProtocol).create_task(
            collet_id,
            TaskCreateCommand(1, OWNER.actor, objective="HTTP Swarm Work"),
            OWNER,
        )
        candidate = dict(
            task_ref=task.task_id,
            swarm_plan_id=_PLAN,
            termination_condition={"kind": "all_stages"},
            stages=[
                {
                    "stage_id": _STAGE,
                    "name": "Deliver the report",
                    "completion_condition": {"kind": "reported"},
                }
            ],
        )
        allocation = dict(
            stage_ref=_STAGE,
            target_kind="AGENT",
            target_ref="participant://worker",
            execution_profile_ref=_PROFILE,
            allocation_id=_ALLOCATION,
            lease_expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        )
        service = world.get(SwarmMethodOpsServiceProtocol)
        if operation == "profile":
            body.update(_PROFILE_BODY)
        else:
            service.declare_execution_profile(
                collet_id, BINDING, SwarmExecutionProfileCommand(**_PROFILE_BODY), OWNER
            )
        if operation == "submit":
            body.update(candidate)
        elif operation != "profile":
            service.submit_plan_candidate(
                collet_id, BINDING, SwarmPlanCandidateCommand(**candidate), OWNER
            )
        if operation in {
            "start",
            "pause",
            "resume",
            "terminate",
            "allocate",
            "complete",
            "fail",
        }:
            service.adopt_plan(collet_id, BINDING, _PLAN, SwarmPlanOpCommand(), OWNER)
        if operation in {
            "pause",
            "resume",
            "terminate",
            "allocate",
            "complete",
            "fail",
        }:
            service.start_plan(collet_id, BINDING, _PLAN, SwarmPlanOpCommand(), OWNER)
        if operation == "resume":
            service.pause_plan(collet_id, BINDING, _PLAN, SwarmPlanOpCommand(), OWNER)
        if operation == "allocate":
            body.update(allocation)
        if operation in {"complete", "fail"}:
            service.allocate_stage(
                collet_id, BINDING, SwarmAllocateCommand(**allocation), OWNER
            )
            body["allocation_refs"] = [_ALLOCATION]
        store, axes = world.get(MethodStoreProtocol), current_axes()
        before["plans"] = store.list_plans(axes, BINDING, collet_id)
        before["stages"] = store.list_stages(axes, BINDING, collet_id)
        before["allocations"] = store.list_allocations(axes, BINDING, collet_id)
        before["profiles"] = store.list_execution_profiles(axes, BINDING, collet_id)
        if not happy:
            params["binding_id"] = "missing_binding"

    def check(response, world):
        store, axes = world.get(MethodStoreProtocol), current_axes()
        collet_id = params["collab_context_id"]
        if not happy or operation == "validate":
            assert store.list_plans(axes, BINDING, collet_id) == before["plans"]
            assert store.list_stages(axes, BINDING, collet_id) == before["stages"]
            assert (
                store.list_allocations(axes, BINDING, collet_id)
                == before["allocations"]
            )
            assert (
                store.list_execution_profiles(axes, BINDING, collet_id)
                == before["profiles"]
            )
        if not happy:
            return
        data = response.json()["data"]
        if operation == "validate":
            assert data == {
                "swarm_plan_id": _PLAN,
                "valid": True,
                "violations": [],
                "stage_count": 1,
            }
        elif operation == "profile":
            assert data["accepted"] and data["object_id"] == _PROFILE
            profile = store.get_execution_profile(axes, _PROFILE, BINDING, collet_id)
            assert profile.target_ref == _PROFILE_BODY["target_ref"]
        elif operation in {"allocate", "complete", "fail"}:
            assert data["accepted"] and data["state"] == state
            stage = store.get_stage(axes, _STAGE, BINDING, collet_id)
            assert stage.state.value == (
                "RUNNING" if operation == "allocate" else state
            )
            [allocation] = store.list_allocations(axes, BINDING, collet_id)
            assert allocation.state.value == (
                "RELEASED" if operation == "complete" else "ACTIVE"
            )
        else:
            plan = store.get_plan(axes, _PLAN, BINDING, collet_id)
            assert plan.state.value == data["state"] == state
            assert plan.transitioned_by == OWNER.actor.principal_id

    endpoint_test(
        method=method,
        path=BASE + "/{binding_id}/swarm/" + suffix,
        scenario="swarm_method_happy" if happy else "swarm_method_missing_binding",
        input=CaseInput(
            path_params=params,
            headers=HEADERS,
            json_body=body if method == "POST" else None,
        ),
        seed=seed,
        expect=ExpectSuccess(json_contains={"code": 200000})
        if happy
        else ExpectError(status=404, json_contains={"code": 404000}),
        extra_assertions=(check,),
    )(lambda: None)


for _method, _suffix, _operation, _state in (
    ("POST", "execution-profiles", "profile", "ACTIVE"),
    ("POST", "plans", "submit", "DRAFT"),
    ("GET", "plans/{plan_id}/validate", "validate", "DRAFT"),
    ("POST", "plans/{plan_id}/adopt", "adopt", "READY"),
    ("POST", "plans/{plan_id}/start", "start", "RUNNING"),
    ("POST", "plans/{plan_id}/pause", "pause", "PAUSED"),
    ("POST", "plans/{plan_id}/resume", "resume", "RUNNING"),
    ("POST", "plans/{plan_id}/terminate", "terminate", "CANCELLED"),
    ("POST", "stages/{stage_id}/allocate", "allocate", "ACTIVE"),
    ("POST", "stages/{stage_id}/complete", "complete", "SUCCEEDED"),
    ("POST", "stages/{stage_id}/fail", "fail", "FAILED"),
):
    for _happy in (True, False):
        _register(_method, _suffix, _operation, _state, _happy)
