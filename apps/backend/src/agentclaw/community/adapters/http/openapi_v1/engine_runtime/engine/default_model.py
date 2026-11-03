"""Default-model read through the addressed bot's live runtime."""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, StrictStr

from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute
from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import resolve_operable_bot
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    DeviceUuidQuery, OwnerIdDep, StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.di import Injected

router = APIRouter(
    prefix="/openapi/v1/bots/{bot_id}/engine", tags=["engine"],
    route_class=PublicAPIRoute,
)


class DefaultModel(BaseModel):
    """The engine's default, independent of the catalogue and session overrides."""

    model_id: StrictStr | None = Field(
        description="Engine-owned default model identifier; null when unconfigured. "
        "Does not include session or request overrides and need not occur in the catalogue.",
        examples=["openai/gpt-5.3", None],
    )


@router.get("/default-model", response_model=Envelope[DefaultModel])
@envelope_errors
async def get_default_model(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    device_uuid: DeviceUuidQuery = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[DefaultModel]:
    """Read the default from the bot's selected live engine, without its config."""
    facts = await resolve_operable_bot(
        relay, bot_id, caller_id=user_id, owner_id=owner_id,
        stage=stage.value, surface="engine",
    )
    result = await relay.call(
        bot_id=bot_id, owner_id=owner_id, facts=facts, stage=stage.value,
        **({"device_uuid": device_uuid} if device_uuid is not None else {}),
        method="GET", path="/api/engine/default-model",
    )
    raw = result.data
    if not isinstance(raw, dict) or "model_id" not in raw:
        raise EngineUpstreamError("invalid default model response")
    model_id = raw["model_id"]
    if model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
        raise EngineUpstreamError("invalid default model identifier")
    return envelope(DefaultModel(model_id=model_id), request)
