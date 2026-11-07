"""Probe the expert's current adapter over the normal device transport."""

from __future__ import annotations

import asyncio
from typing import Any

from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterEndpointNotFoundError


def creation_adapter_ready(*, spec: dict[str, Any], bot: dict[str, Any], bot_id: str,
                           owner_id: str, resolver_provider: Any, transport_provider: Any,
                           concrete_connection_provider: Any) -> bool:
    if spec.get("expert_origin") is None:
        return True
    return expert_adapter_ready(
        bot=bot, bot_id=bot_id, owner_id=owner_id,
        resolver=resolver_provider(), transport=transport_provider(),
        concrete_connection=concrete_connection_provider(),
    )


def expert_adapter_ready(
    *, bot: dict[str, Any], bot_id: str, owner_id: str,
    resolver: DeviceContextResolver, transport: Any, concrete_connection: bool = False,
) -> bool:
    binding_id = bot.get("binding_id")
    if not binding_id:
        return False
    resolve = (
        resolver.resolve_for_binding
        if concrete_connection
        else resolver.resolve_for_binding_invoke
    )
    context = resolve(
        int(binding_id), owner_id, bot_id=bot_id,
    )
    conn_info = {**context.conn_info, "probe_runtime_readiness": True}

    async def probe() -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                transport.invoke(conn_info, "GET", "/readiness", timeout=3.0),
                timeout=3.0,
            )
        except DeviceAdapterEndpointNotFoundError as exc:
            if not exc.standard_route_missing:
                raise
            # Older adapters lack /readiness. Do not treat 404 as ready: the
            # same authenticated route must successfully answer /health.
            health = await asyncio.wait_for(
                transport.invoke(conn_info, "GET", "/health", timeout=3.0),
                timeout=3.0,
            )
            return {"state": "ready"} if health.get("status") == "ok" else {}

    response = asyncio.run(probe())
    return response.get("state") == "ready"
