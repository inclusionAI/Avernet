"""DeviceAdapterTransport -- HTTP transport to a bot's engine adapter.

Abstracts the single system boundary that ``CronRelayService`` (and any
future relay) crosses: issuing an HTTP request against a bot's engine
adapter, given the ``conn_info`` resolved from
``DeviceService.get_device_connection_v2``.

This is the *only* part of the relay that cannot run in tests — the
real adapter process isn't reachable. Everything above it (device
resolution, permission checks, response shaping) is production code and
runs for real. Implementations:

- ``plugins.prod.device_adapter_transport.HttpDeviceAdapterTransport``
  — builds the adapter URL from ``conn_info`` and forwards over httpx.
- ``plugins.local.device_adapter_transport.InMemoryDeviceAdapterTransport``
  — a stateful in-memory cron adapter used by injection tests and local
  boots, so the real relay exercises end-to-end without a live adapter.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


@runtime_checkable
class DeviceAdapterTransport(Plugin, Protocol):
    """Forward an HTTP request to a bot's engine adapter."""

    async def invoke(
        self,
        conn_info: dict[str, Any],
        method: str,
        path: str,
        body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Issue ``method path`` against the adapter described by ``conn_info``.

        Args:
            conn_info: Connection info from
                ``DeviceService.get_device_connection_v2`` — carries the
                base ``url``/``target``, ``headers``, and device type.
            method: HTTP method (GET/POST/PUT/DELETE).
            path: Adapter path, e.g. ``/api/cron`` or ``/api/cron/{id}/run``.
            body: JSON request body, if any.
            params: Query parameters, if any.

        Returns:
            The adapter's parsed JSON response (an envelope dict, usually
            ``{"success": bool, "data": ...}``).

        Raises:
            ValueError: On transport/HTTP failure (prod impl), so callers
                can surface a uniform error envelope.
        """
        ...
