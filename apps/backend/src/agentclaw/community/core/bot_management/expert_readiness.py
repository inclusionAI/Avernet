"""Probe the expert's current adapter over the normal device transport."""

from __future__ import annotations

import asyncio
from typing import Any

from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver


def creation_adapter_ready(*, spec: dict[str, Any], bot: dict[str, Any], bot_id: str,
                           owner_id: str, resolver_provider: Any, transport_provider: Any) -> bool:
    if spec.get("expert_origin") is None:
        return True
    return expert_adapter_ready(
        bot=bot, bot_id=bot_id, owner_id=owner_id,
        resolver=resolver_provider(), transport=transport_provider(),
    )


def expert_adapter_ready(
    *, bot: dict[str, Any], bot_id: str, owner_id: str,
    resolver: DeviceContextResolver, transport: Any,
) -> bool:
    binding_id = bot.get("binding_id")
    if not binding_id:
        return False
    context = resolver.resolve_for_binding_invoke(
        int(binding_id), owner_id, bot_id=bot_id,
    )

    async def probe() -> dict[str, Any]:
        return await asyncio.wait_for(
            transport.invoke(context.conn_info, "GET", "/readiness", timeout=3.0),
            timeout=3.0,
        )

    response = asyncio.run(probe())
    return response.get("state") == "ready"
