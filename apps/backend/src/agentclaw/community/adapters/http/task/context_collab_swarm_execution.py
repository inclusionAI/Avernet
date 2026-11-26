"""Authenticated, thin HTTP delivery for bounded Swarm Stage execution."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.task.task_commission_service import (
    TaskCommissionServiceProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    InteractionCommand,
    StepCommand,
)
from agentclaw.community.di import Injected

router = APIRouter()
_PATH = "/context_collab/{collab_context_id}/method-bindings/{binding_id}/swarm"


class StepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)


class InteractionRequest(StepRequest):
    stage_ref: str = Field(min_length=1)
    session_ref: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=16000)
    kind: Literal["message", "clarification_reply"] = "message"
    clarification_ref: str | None = None


def _actor(user):
    return ActorContext(PrincipalRef("human", user.staffId), source="api")


@router.post(_PATH + "/step", response_model=Envelope[dict])
@envelope_errors
async def step_swarm(
    collab_context_id: str,
    binding_id: str,
    body: StepRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskCommissionServiceProtocol = Injected(TaskCommissionServiceProtocol),  # noqa: B008
) -> Envelope[dict]:
    return envelope(
        await asyncio.to_thread(
            service.step_swarm,
            collab_context_id,
            binding_id,
            StepCommand(**body.model_dump()),
            _actor(user),
        ),
        request,
    )


@router.get(_PATH + "/stages", response_model=Envelope[dict])
@envelope_errors
async def get_swarm_stages(
    collab_context_id: str,
    binding_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskCommissionServiceProtocol = Injected(TaskCommissionServiceProtocol),  # noqa: B008
) -> Envelope[dict]:
    return envelope(
        await asyncio.to_thread(
            service.get_swarm_stages, collab_context_id, binding_id, _actor(user)
        ),
        request,
    )


@router.post(_PATH + "/interactions", response_model=Envelope[dict])
@envelope_errors
async def interact_swarm(
    collab_context_id: str,
    binding_id: str,
    body: InteractionRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskCommissionServiceProtocol = Injected(TaskCommissionServiceProtocol),  # noqa: B008
) -> Envelope[dict]:
    return envelope(
        await asyncio.to_thread(
            service.interact_swarm,
            collab_context_id,
            binding_id,
            InteractionCommand(**body.model_dump()),
            _actor(user),
        ),
        request,
    )
