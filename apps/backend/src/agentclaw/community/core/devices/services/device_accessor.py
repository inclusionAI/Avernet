"""DeviceAccessor -- device allocation and query backend interface.

A plain ``core`` seam (not a plugin): the per-provider implementations live
alongside it under ``core/devices/services`` and are selected by DI.

Current implementations:
- ``core.devices.services.local_device_accessor.LocalDeviceAccessor``
- ``core.devices.services.arca_device_accessor.ArcaDeviceAccessor``
- ``core.devices.services.baas_device_accessor.BaasDeviceAccessor``
- ``core.devices.services.teclaw_device_accessor.TeclawDeviceAccessor``
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DeviceAccessor(Protocol):
    """Allocate / release / inspect a device for a bot."""

    def get_connection_info(
        self, bot_id: str, user_id: str
    ) -> dict[str, Any] | None:
        """Get device connection details for proxy communication.

        Args:
            bot_id: The bot identifier.
            user_id: The requesting user identifier.

        Returns:
            Connection dict with keys: url, token, sandbox_id, use_proxy.
            Returns None if bot has no device or connection cannot be established.
        """
        ...

    def get_engine_config_path(
        self,
        bot_id: str,
        owner_id: str,
        *,
        entity_id: str,
        engine_type: str,
        entity_type: str = "staff",
    ) -> str:
        """Resolve the absolute path of the engine config file
        (``openclaw.json``) for this bot on its device.

        Strictness varies by impl:
        - **Local**: returns the standard per-bot engine-dir layout, matching
          arca / baas in singlebox.
        - **Arca / Baas**: returns the sandbox-view engine-dir path
          (``path_factory.get_bot_engine_dir(...)/openclaw.json``).

        Args:
            bot_id: The bot identifier.
            owner_id: The owner user identifier (NOT the operator).
            entity_id: Entity the bot belongs to.
            engine_type: Engine type (e.g. ``openclaw``, ``moltis``).
            entity_type: Entity type, default ``staff``.

        Returns:
            Absolute device-view path to ``openclaw.json``.
        """
        ...
