"""Device callback utility — DI-managed handler for PublishService delegation.

Provides ``DeviceCallbackHandler``, a thin async wrapper around
``PublishService.handle_device_callback``. The publish callback target is received
via constructor injection as a lazy factory returning a ``PublishService``. The
factory defers resolution of the concrete ``PublishService`` until a callback
actually arrives, keeping the ``device_service ↔ publish_service`` graph acyclic.

Callers should receive a ``DeviceCallbackHandler`` via their own constructor
injection instead of reaching for ``get_container`` or ``PublishService`` directly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from secbaas.community.api.publish_manage import (
        DeviceCallbackRequest,
        PublishService,
    )


class DeviceCallbackHandler:
    """Handle a device callback by delegating to an injected publish callback.

    The callback executor is injected as a zero-argument lazy factory, resolved
    only when a callback is actually handled. This keeps the object graph acyclic
    while still acquiring the dependency through constructor injection.

    Args:
        publish_service_factory: Zero-argument callable returning a ``PublishService``
            (e.g. ``lambda: publish_service``).
    """

    def __init__(self, publish_service_factory: Callable[[], PublishService]) -> None:
        self._publish_service_factory = publish_service_factory

    async def handle(self, request: DeviceCallbackRequest) -> dict[str, str]:
        """Delegate a device callback to the resolved executor.

        Args:
            request: The device callback request with result details.

        Returns:
            A status dict from the executor (typically ``{"status": "ok"}``).
        """
        return await self._publish_service_factory().handle_device_callback(request)
