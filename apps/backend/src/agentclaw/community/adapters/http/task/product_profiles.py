"""Thin authenticated HTTP delivery of the five-method product Service API."""

from dataclasses import asdict
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.task.product_profile_service import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    decode_composition,
    encode_profile,
)
from agentclaw.community.adapters.http.task.product_body_limits import (
    ProductBodyLimitRoute,
)
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/api/v1/collaboration/task/products",
    tags=["product-profiles"],
    route_class=ProductBodyLimitRoute,
)


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(
        min_length=1, max_length=128, description="Scoped idempotency key."
    )


class ProfileCreate(Mutation):
    name: str = Field(
        min_length=1,
        max_length=256,
        description="Immutable product configuration name.",
    )
    composition: dict = Field(
        description="Closed MethodCompositionTemplate value codec."
    )
    stages: str = Field(
        min_length=1,
        max_length=262144,
        description="Strict stages YAML; sole stage graph input.",
    )


class ColletCreate(Mutation):
    profile_id: int = Field(
        gt=0, description="Exact persisted ProductProfile identity."
    )
    content: str = Field(
        min_length=1, max_length=262144, description="Collaboration request content."
    )


class Interaction(Mutation):
    interactive_context: str = Field(
        min_length=1,
        max_length=262144,
        description="Feedback for the trusted interaction capability.",
    )
    expected_revision: int = Field(
        gt=0, description="Revision returned by the rollout query."
    )


def actor(user):
    return ActorContext(PrincipalRef("human", user.staffId), "api")


@router.post("/profiles", response_model=Envelope[dict])
@envelope_errors
async def create_profile(
    private_body: ProfileCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.create_product_profile(
        private_body.name,
        decode_composition(private_body.composition),
        private_body.stages,
        context=actor(user),
        request_id=private_body.request_id,
    )
    return envelope(encode_profile(result), request)


@router.post("/collets", response_model=Envelope[dict])
@envelope_errors
async def create_collet(
    private_body: ColletCreate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.create_product_collet(
        private_body.profile_id,
        private_body.content,
        context=actor(user),
        request_id=private_body.request_id,
    )
    return envelope(asdict(result), request)


@router.get("/collets/{collet_id}/rollouts", response_model=Envelope[dict])
@envelope_errors
async def query_rollouts(
    collet_id: int,
    request: Request,
    all_rollout: bool = True,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.query_collet_rollout(
        collet_id, all_rollout, context=actor(user)
    )
    return envelope(
        {
            "rollouts": [asdict(r) for r in result],
            "revision": result.revision,
            "current_rollout_id": result.current_rollout_id,
            "stopped": result.stopped,
        },
        request,
    )


@router.post("/rollouts/{rollout_id}/interactive", response_model=Envelope[dict])
@envelope_errors
async def interactive(
    rollout_id: int,
    private_body: Interaction,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.interactive(
        rollout_id,
        private_body.interactive_context,
        context=actor(user),
        request_id=private_body.request_id,
        expected_revision=private_body.expected_revision,
    )
    return envelope(asdict(result), request)


@router.get("/stages/{stage_rollout_id}/messages", response_model=Envelope[dict])
@envelope_errors
async def stage_messages(
    stage_rollout_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    user: AuthenticatedUser = Depends(get_current_user),
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.query_stage_rollout_details(
        stage_rollout_id, context=actor(user), limit=limit
    )
    return envelope(asdict(result), request)
