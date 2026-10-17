"""Read-only Bot Engine release inspection."""

from typing import Annotated
from fastapi import APIRouter, Query, Request

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    OwnerIdDep,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_version_service import EngineVersionServiceProtocol
from agentclaw.community.di import Injected
from .schemas_engine_version import EngineVersionObservation

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}", tags=["bots"], route_class=PublicAPIRoute
)


@router.get(
    "/engine-version",
    response_model=Envelope[EngineVersionObservation],
    response_model_exclude_none=True,
)
@envelope_errors
async def get_engine_version(
    bot_id: BotIdPath,
    request: Request,
    owner_id: OwnerIdDep,
    caller_id: UserIdDep,
    stage: Annotated[
        str,
        Query(
            description="Workspace stage. Only draft is supported; verify and online require the publication workflow."
        ),
    ] = "draft",
    device_uuid: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=255,
            description="Select one existing instance; required if the workspace has multiple instances.",
        ),
    ] = None,
    service: EngineVersionServiceProtocol = Injected(EngineVersionServiceProtocol),
) -> Envelope[EngineVersionObservation]:
    """Inspect the Engine release and return advisory workspace restart guidance.

    Personal Bots and service-Bot drafts only. Published runtimes require the
    publication workflow. Only OpenClaw/Hermes are probed; other engines return
    not_applicable without device access. Advice is independent of provider and
    image policy. The query performs no lifecycle or image-policy writes.
    """
    result = await service.inspect(
        bot_id=bot_id,
        owner_id=owner_id,
        caller_id=caller_id,
        stage=stage,
        device_uuid=device_uuid,
    )
    return envelope(EngineVersionObservation.model_validate(result), request)
