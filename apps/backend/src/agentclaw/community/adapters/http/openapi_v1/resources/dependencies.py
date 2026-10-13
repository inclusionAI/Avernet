"""Resolve an explicitly addressed resource workspace once per request."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import Depends, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import BotIdPath
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import (
    RuntimeStage,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.params import (
    DeviceUuidQuery,
    OwnerIdDep,
    StageQuery,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.engine_runtime.stage import (
    require_stage_writable,
    resolve_stage_device_context,
)
from agentclaw.community.core.engine_runtime.errors import EngineResourceNotFoundError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import (
    DeviceBindingRepository,
)
from agentclaw.community.core.repository.protocols.publishing import (
    BotPublishRepositoryProtocol,
)
from agentclaw.community.di import Injected


async def resource_target(
    bot_id: BotIdPath,
    owner_id: OwnerIdDep,
    request: Request,
    stage: StageQuery = RuntimeStage.DRAFT,
    device_uuid: DeviceUuidQuery = None,
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    publications: BotPublishRepositoryProtocol = Injected(BotPublishRepositoryProtocol),
    bindings: DeviceBindingRepository = Injected(DeviceBindingRepository),
    bots: BotRepository = Injected(BotRepository),
) -> dict[str, Any]:
    if stage is RuntimeStage.DRAFT and device_uuid is None:
        return {}
    # COSEC: selecting an instance must not allow published-workspace writes.
    if request.method not in {"GET", "HEAD"}:
        require_stage_writable(stage.value)
    context = await asyncio.to_thread(
        resolve_stage_device_context,
        resolver,
        publications,
        bindings,
        bots,
        bot_id=bot_id,
        owner_id=owner_id,
        stage=stage.value,
        device_uuid=device_uuid,
    )
    if device_uuid is not None and (
        context.provider not in {"baas", "teclaw"}
        or context.bot_type == "desktop"
    ):
        raise EngineResourceNotFoundError("instance selection is unavailable")
    return {"runtime_context": context}


ResourceTargetDep = Annotated[dict[str, Any], Depends(resource_target)]
