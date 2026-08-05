"""Community no-op ``DeviceAdapterTransport``.

Community ships no container runtime, so there is no engine adapter to relay to
(BaaS-team-owned, out of B6 scope). This impl exists so ``CronRelayService`` (a
base-list cron service that injects ``DeviceAdapterTransport``) stays
constructable in the community profile; ``invoke`` makes no network call and
returns a uniform failure envelope. Real impl (not a ``MockSeam``); imports only
``plugin_api``, so it satisfies the community column isolation guard.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Optional

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterStreamResponse,
    DeviceAdapterTransport,
)

logger = get_logger()


class CommunityDeviceAdapterTransport(DeviceAdapterTransport):
    """No-op adapter transport for the community profile (no device runtime)."""

    async def invoke(
        self,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        logger.warning(
            "[CommunityDeviceAdapterTransport] no-op (community has no device "
            "runtime): %s %s",
            method,
            path,
        )
        return {
            "success": False,
            "message": "community mode — no device adapter (no container runtime)",
        }

    async def stream(
        self,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        *,
        timeout: float | None = None,
    ) -> DeviceAdapterStreamResponse:
        logger.warning(
            "[CommunityDeviceAdapterTransport] stream unavailable: %s %s",
            method,
            path,
        )

        async def error_body() -> AsyncIterator[bytes]:
            yield b'{"detail":"device adapter stream unavailable"}'

        async def close() -> None:
            return None

        return DeviceAdapterStreamResponse(
            status_code=503,
            headers={"content-type": "application/json"},
            body=error_body(),
            close=close,
        )
