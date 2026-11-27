"""Thin authenticated HTTP delivery of the five-method product Service API."""

from dataclasses import asdict
from typing import AsyncIterator
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
    actor_context_scope,
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
from .product_rollout_schemas import RolloutQueryResponse
from agentclaw.community.di import Injected


async def authenticated_actor(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AsyncIterator[None]:
    with actor_context_scope(ActorContext(PrincipalRef("human", user.staffId), "api")):
        yield


router = APIRouter(
    prefix="/api/v1/collaboration/task/products",
    tags=["product-profiles"],
    route_class=ProductBodyLimitRoute,
    dependencies=[Depends(authenticated_actor)],
)


class ProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProfileCreate(ProductRequest):
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


class ColletCreate(ProductRequest):
    profile_id: int = Field(
        gt=0, description="Exact persisted ProductProfile identity."
    )
    content: str = Field(
        min_length=1, max_length=262144, description="Collaboration request content."
    )


class Interaction(ProductRequest):
    interactive_uuid: str = Field(
        min_length=1,
        max_length=128,
        description="Unique intervention ID; reuse only for identical retries.",
    )
    interactive_context: str = Field(
        min_length=1,
        max_length=262144,
        description="Feedback for the trusted interaction capability.",
    )


@router.post("/profiles", response_model=Envelope[dict])
@envelope_errors
async def create_profile(
    private_body: ProfileCreate,
    request: Request,
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.create_product_profile(
        private_body.name,
        decode_composition(private_body.composition),
        private_body.stages,
    )
    return envelope(encode_profile(result), request)


@router.post("/collets", response_model=Envelope[dict])
@envelope_errors
async def create_collet(
    private_body: ColletCreate,
    request: Request,
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.create_product_collet(
        private_body.profile_id,
        private_body.content,
    )
    return envelope(asdict(result), request)


@router.get(
    "/collets/{collet_id}/rollouts", response_model=Envelope[RolloutQueryResponse]
)
@envelope_errors
async def query_rollouts(
    collet_id: int,
    request: Request,
    all_rollout: bool = True,
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.query_collet_rollout(collet_id, all_rollout)
    return envelope(
        {
            "rollouts": [asdict(r) for r in result],
            "revision": result.revision,
            "current_rollout_id": result.current_rollout_id,
            "stopped": result.stopped,
            **(
                {"multi_rollout_control": result.multi_rollout_control}
                if result.multi_rollout_control is not None
                else {}
            ),
        },
        request,
    )


@router.post("/rollouts/{rollout_id}/interactive", response_model=Envelope[dict])
@envelope_errors
async def interactive(
    rollout_id: int,
    private_body: Interaction,
    request: Request,
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.interactive(
        rollout_id,
        private_body.interactive_uuid,
        private_body.interactive_context,
    )
    return envelope(asdict(result), request)


@router.get("/stages/{stage_rollout_id}/messages", response_model=Envelope[dict])
@envelope_errors
async def stage_messages(
    stage_rollout_id: int,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    result = await service.query_stage_rollout_details(stage_rollout_id, limit=limit)
    return envelope(asdict(result), request)
