"""Public service-Bot container list and single-instance restart endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import OwnerIdDep
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    accepted,
    envelope,
    envelope_errors,
)
from agentclaw.community.api.service_publication_facade import (
    ServicePublicationFacadeProtocol,
)
from agentclaw.community.di import Injected

from .schemas import (
    ContainerInstance,
    ContainerList,
    ContainerRestart,
    ContainerScale,
    ContainerScaleApproval,
    ContainerScaleRequest,
    ContainerSummary,
    ContainerStatus,
    InstanceIdPath,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute


router = APIRouter(prefix="/openapi/v1/bots/{bot_id}/containers", route_class=PublicAPIRoute)

_STATUS_MAP: dict[str, ContainerStatus] = {
    "ACTIVE": "healthy",
    "RESTARTING": "restarting",
    "ABNORMAL": "abnormal",
    "UNKNOWN": "unknown",
}


def _project_instance(raw: dict[str, Any]) -> ContainerInstance:
    return ContainerInstance(
        id=str(raw.get("device_uuid") or ""),
        status=_STATUS_MAP.get(str(raw.get("health_status") or "").upper(), "unknown"),
        internal_status=raw.get("status"),
        engine=str(raw.get("engine_type") or "openclaw"),
        provider=raw.get("provider_type"),
        provider_instance_id=raw.get("provider_device_id"),
        created_at=raw.get("gmt_create"),
    )


def _project_scale_approval(raw: dict[str, Any] | None) -> ContainerScaleApproval | None:
    if not isinstance(raw, dict):
        return None
    return ContainerScaleApproval(
        status=str(raw.get("status") or "UNKNOWN"),
        approval_url=raw.get("approval_url"),
        current_count=raw.get("current_count"),
        delta=raw.get("delta"),
        target_count=raw.get("target_count"),
    )


def _project_list(raw: dict[str, Any]) -> ContainerList:
    instances = [_project_instance(item) for item in raw.get("instances", [])]
    counts = {status: 0 for status in _STATUS_MAP.values()}
    for instance in instances:
        counts[instance.status] += 1
    return ContainerList(
        bot_id=str(raw["bot_id"]),
        summary=ContainerSummary(
            total=len(instances),
            healthy=counts["healthy"],
            abnormal=counts["abnormal"],
            restarting=counts["restarting"],
            unknown=counts["unknown"],
        ),
        instances=instances,
        scale_approval=_project_scale_approval(raw.get("scale_approval")),
    )


@router.get("", response_model=Envelope[ContainerList])
@envelope_errors
async def list_containers(
    bot_id: BotIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ContainerList]:
    """Return the current online instance snapshot for a service Bot."""
    result = facade.list_containers(
        bot_id,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return envelope(_project_list(result), request)


@router.post(
    "/scale",
    status_code=202,
    response_model=Envelope[ContainerScale],
)
@envelope_errors
async def scale_containers(
    bot_id: BotIdPath,
    body: ContainerScaleRequest,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ContainerScale]:
    """Request a delta-based scale; execution waits for approval."""
    result = await facade.scale_containers(
        bot_id,
        delta=body.delta,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return accepted(
        ContainerScale(
            bot_id=str(result["bot_id"]),
            accepted=bool(result["accepted"]),
            current_count=int(result["current_count"]),
            delta=int(result["delta"]),
            target_count=int(result["target_count"]),
            approval=_project_scale_approval(result["approval"]),
        ),
        request,
    )


@router.post(
    "/{instance_id}/restart",
    status_code=202,
    response_model=Envelope[ContainerRestart],
)
@envelope_errors
async def restart_container(
    bot_id: BotIdPath,
    instance_id: InstanceIdPath,
    request: Request,
    actor_id: UserIdDep,
    owner_id: OwnerIdDep,
    facade: ServicePublicationFacadeProtocol = Injected(
        ServicePublicationFacadeProtocol
    ),
) -> Envelope[ContainerRestart]:
    """Restart one abnormal instance; service-Bot owner only."""
    result = facade.restart_container(
        bot_id,
        instance_id,
        actor_id=actor_id,
        owner_id=owner_id,
    )
    return accepted(ContainerRestart.model_validate(result), request)
