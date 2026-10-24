"""The addressed runtime's effective new-session default model."""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath, Envelope
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.gating import (
    resolve_operable_bot,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    DeviceUuidQuery,
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.adapters.http.openapi_v1.principal import UserIdDep
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.core.engine_runtime.errors import EngineUpstreamError
from agentclaw.community.di import Injected


class EngineDefaultModel(BaseModel):
    """The engine-resolved model inherited by a new ordinary session."""

    model_id: str = Field(
        min_length=1,
        description="Effective model for a new ordinary session without a model override, "
        "including engine inheritance. Excludes existing-session overrides and failover.",
    )


@envelope_errors
async def get_engine_default_model(
    bot_id: BotIdPath,
    user_id: UserIdDep,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    device_uuid: DeviceUuidQuery = None,
    relay: EngineRuntimeRelayProtocol = Injected(EngineRuntimeRelayProtocol),
) -> Envelope[EngineDefaultModel]:
    """Read the engine-resolved default without creating or modifying a session."""
    facts = await resolve_operable_bot(
        relay,
        bot_id,
        caller_id=user_id,
        owner_id=owner_id,
        stage=stage.value,
        surface="engine",
    )
    result = await relay.call(
        bot_id=bot_id,
        owner_id=owner_id,
        facts=facts,
        stage=stage.value,
        **({"device_uuid": device_uuid} if device_uuid is not None else {}),
        method="GET",
        path="/api/engine/default-model",
    )
    model_id = result.data.get("id") if isinstance(result.data, dict) else None
    if (
        not isinstance(model_id, str)
        or not model_id.strip()
        or model_id != model_id.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in model_id)
    ):
        raise EngineUpstreamError("invalid default model reference")
    return envelope(EngineDefaultModel(model_id=model_id), request)
