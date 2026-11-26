"""Signed Bot-only progress ingress, separate from the five product operations."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.commission_a2a_auth import (
    require_commission_bot,
)
from agentclaw.community.api.task.rollout_progress_service import (
    RolloutProgressServiceProtocol,
)
from agentclaw.community.core.gateway_principal import GatewayBot
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.progress_event import (
    ProgressEvent,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ReportContext,
)
from agentclaw.community.adapters.http.task.product_body_limits import (
    ProductBodyLimitRoute,
)
from agentclaw.community.di import Injected
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

router = APIRouter(tags=["product-progress"], route_class=ProductBodyLimitRoute)


class ProgressReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event: dict = Field(
        description="Closed ProgressEvent value; no actor or tenant fields."
    )
    request_id: str = Field(
        min_length=1, max_length=128, description="Scoped report idempotency key."
    )
    attempt: str = Field(
        min_length=1,
        max_length=128,
        description="Claimed attempt; checked against persisted allocation.",
    )
    expected_revision: int = Field(
        gt=0, description="Claimed current aggregate revision; checked by CAS."
    )


@router.post(
    "/api/v1/collaboration/task/product-progress",
    response_model=Envelope[dict],
    openapi_extra={"x-avernet-security": {"bot": "required"}},
)
@envelope_errors
async def report_progress(
    private_body: ProgressReport,
    request: Request,
    bot: GatewayBot = Depends(require_commission_bot),
    service: RolloutProgressServiceProtocol = Injected(RolloutProgressServiceProtocol),
) -> Envelope[dict]:
    # The dependency verifies issuer/audience/signature/expiry and exactly one
    # Bot. Never promote its owner, nor accept a body or query tenant/actor.
    with avernet_tenant_scope(bot.tenant):
        result = await service.report(
            ProgressEvent.from_dict(private_body.event),
            ReportContext(
                ActorContext(
                    PrincipalRef("bot", bot.bot_uuid),
                    "product_progress",
                    accountable_principal_ref=PrincipalRef("human", bot.owner_id),
                ),
                private_body.request_id,
                private_body.attempt,
                private_body.expected_revision,
            ),
        )
    return envelope(result, request)
