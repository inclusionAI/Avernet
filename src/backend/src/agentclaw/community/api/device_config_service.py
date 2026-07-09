"""Service API Protocol for device-config (allocation lists, provider selection).

Mirrors the public surface of
:class:`agentclaw.community.core.system_config.DeviceConfigService`. DI resolves
the Protocol to the concrete binding in
:mod:`agentclaw.community.di.modules.system_config_module`.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DeviceConfigServiceProtocol(Protocol):
    """Service API for device-allocation configuration."""

    def get_allocation_provider(
        self,
        *,
        staff_id: str,
        env: str,
        template_type: str | None = None,
        bot_type: str | None = None,
    ) -> str: ...

    def add_to_allocation_list(
        self,
        *,
        provider: str,
        staff_ids: list[str],
        env: str,
        creator: str | None = None,
    ) -> int: ...

    def remove_from_allocation_list(
        self,
        *,
        provider: str,
        staff_ids: list[str],
        env: str,
        operator: str | None = None,
    ) -> int: ...

    def get_allocation_lists(self, *, env: str) -> dict[str, Any]: ...

    def set_default_provider(
        self, *, provider: str, env: str, creator: str | None = None
    ) -> None: ...

    def set_template_type_provider_map(
        self, *, mapping: dict[str, str], env: str, creator: str | None = None
    ) -> None: ...

    def set_personal_bot_baas_disable(
        self, *, disabled: bool, env: str, creator: str | None = None
    ) -> None: ...

    def get_max_devices(self, *, env: str) -> int: ...

    def set_max_devices(
        self, *, max_devices: int, env: str, creator: str | None = None
    ) -> None: ...

    def get_allocation_mode(self, *, env: str) -> str: ...

    def set_allocation_mode(
        self, *, mode: str, env: str, creator: str | None = None
    ) -> None: ...
