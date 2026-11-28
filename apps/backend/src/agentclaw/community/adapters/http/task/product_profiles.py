"""Thin authenticated HTTP delivery of the six-method product Service API."""

from dataclasses import asdict
from typing import AsyncIterator, Literal
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.response_envelope import trace_id
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
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfileStatus,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_profile,
    encode_profile_response,
)
from agentclaw.community.adapters.http.task.product_body_limits import (
    ProductBodyLimitRoute,
)
from .product_rollout_schemas import RolloutQueryResponse
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger


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


logger = get_logger()


def _payload_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except Exception:
        return 0


class ProfileCreate(ProductRequest):
    name: str = Field(
        min_length=1,
        max_length=256,
        description="ProductProfile configuration name.",
    )
    stages: str = Field(
        min_length=1,
        max_length=262144,
        description="Static Swarm workflow YAML; creation installs human-feedback MultiRollout.",
    )


class ProfileUpdate(ProductRequest):
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Optional new ProductProfile name.",
    )
    description: str | None = Field(
        default=None,
        max_length=2048,
        description="Optional new ProductProfile description; empty string is valid.",
    )
    stages: str | None = Field(
        default=None,
        min_length=1,
        max_length=262144,
        description="Optional static Swarm workflow YAML; only DRAFT Profiles accept this change.",
    )
    status: Literal["ONLINE", "OFFLINE"] | None = Field(
        default=None,
        description="Lifecycle transition to request; DRAFT must be sent only by create.",
    )

    @model_validator(mode="after")
    def require_at_least_one_change(self):
        if all(
            value is None
            for value in (self.name, self.description, self.stages, self.status)
        ):
            raise ValueError("profile update requires at least one field")
        return self


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
    logger.info(
        "product_profile.http.create.start path=%r request_id=%r caller_request_id=%r stage_bytes=%d",
        request.url.path,
        trace_id(request),
        request.headers.get("X-Request-ID", ""),
        _payload_size(private_body.stages),
    )
    result = await service.create_product_profile(
        private_body.name,
        private_body.stages,
    )
    return envelope(encode_profile(result), request)


@router.patch("/profiles/{profile_id}", response_model=Envelope[dict])
@envelope_errors
async def update_profile(
    profile_id: int,
    private_body: ProfileUpdate,
    request: Request,
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    logger.info(
        "product_profile.http.update.start path=%r request_id=%r caller_request_id=%r profile_id=%d fields=%r stage_bytes=%d",
        request.url.path,
        trace_id(request),
        request.headers.get("X-Request-ID", ""),
        profile_id,
        sorted(private_body.model_fields_set),
        _payload_size(private_body.stages or ""),
    )
    result = await service.update_product_profile(
        profile_id,
        name=private_body.name,
        description=private_body.description,
        stages=private_body.stages,
        status=ProductProfileStatus(private_body.status)
        if private_body.status is not None
        else None,
    )
    return envelope(encode_profile_response(result), request)


@router.post("/collets", response_model=Envelope[dict])
@envelope_errors
async def create_collet(
    private_body: ColletCreate,
    request: Request,
    service: ProductProfileServiceProtocol = Injected(ProductProfileServiceProtocol),
) -> Envelope[dict]:
    logger.info(
        "product_collet.http.create.start path=%r request_id=%r caller_request_id=%r profile_id=%d content_bytes=%d",
        request.url.path,
        trace_id(request),
        request.headers.get("X-Request-ID", ""),
        private_body.profile_id,
        _payload_size(private_body.content),
    )
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
