"""HTTP target validation and verified actor context for routines only."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from pydantic import ValidationError

from agentclaw.community.adapters.http.cron.schemas import CronTargetRequest
from agentclaw.community.adapters.http.openapi_v1.dependencies import Principal, require_principal
from agentclaw.community.adapters.http.openapi_v1.principal import (
    ActingCallerDep, caller_names_a_user, caller_owner_id,
)

RuntimeStageQuery = Annotated[str | None, Query(description="Runtime: draft, verify or online. Omit on list to aggregate available runtimes.")]
DeviceUuidQuery = Annotated[str | None, Query(description="Existing device UUID within the selected runtime binding.")]
OperatorIdQuery = Annotated[str | None, Query(description="Caller identity to select; requires runtime_stage. Selecting another user requires Bot ADMIN.")]


@dataclass(frozen=True)
class RoutineActor:
    user_id: str
    principal_type: str
    app_id: int | None = None


async def resolve_routine_actor(
    caller: ActingCallerDep,
    principal: Annotated[Principal, Depends(require_principal)],
) -> RoutineActor:
    # COSEC: user_id bypass must never change the real human used for delegation.
    if caller_names_a_user(principal):
        return RoutineActor(caller_owner_id(principal), "user")
    return RoutineActor(caller.user_id, "app", caller.app_id)


RoutineActorDep = Annotated[RoutineActor, Depends(resolve_routine_actor)]


def validate_target(runtime_stage, device_uuid, operator_id) -> CronTargetRequest:
    try:
        return CronTargetRequest(runtime_stage=runtime_stage, device_uuid=device_uuid,
                                 operator_id=operator_id)
    except ValidationError:
        raise HTTPException(400, "Invalid Cron runtime target") from None


def target_parameters(runtime_stage, device_uuid, operator_id, *, actor, user_id,
                      default_stage=None) -> dict:
    target = validate_target(runtime_stage, device_uuid, operator_id)
    return dict(runtime_stage=target.runtime_stage or default_stage,
                device_uuid=target.device_uuid, operator_id=target.operator_id,
                authenticated_user_id=actor.user_id if actor else user_id)
