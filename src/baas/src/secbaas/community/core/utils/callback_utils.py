"""Device callback utility — lazy container resolution for circular DI.

Provides a thin async wrapper around ``PublishService.handle_device_callback``
that defers container access to call-time, avoiding import-time circular
dependency issues between ``device_service ↔ publish_service``.

Callers should use this function instead of reaching for ``get_container``
or ``PublishService`` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from secbaas.community.api.publish_manage import DeviceCallbackRequest


async def handle_device_callback(request: DeviceCallbackRequest) -> dict[str, str]:
    """Handle a device callback by delegating to ``PublishService``.

    Resolves ``publish_service`` from the DI container lazily to break
    the circular dependency chain: ``device_service ↔ publish_service``.

    Args:
        request: The device callback request with result details.

    Returns:
        A status dict from the publish service (typically ``{"status": "ok"}``).
    """
    from secbaas.community.bootstrap import get_container

    return (
        await get_container().services.publish_service().handle_device_callback(request)
    )
