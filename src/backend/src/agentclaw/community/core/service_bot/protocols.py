"""Core-local ports consumed by the service-Bot domain."""

from __future__ import annotations

from typing import Any, Protocol

from agentclaw.community.core.devices.models import OperatorContext


class ServiceRuntimeDevicePort(Protocol):
    """Device operations needed by the service publication facade."""

    def get_instances_by_bot(
        self,
        *,
        bot_id: str,
        health_check: bool = False,
    ) -> dict[str, Any]: ...

    def restart_device_by_bot(
        self,
        *,
        bot_id: str,
        device_uuid: str,
        operator: OperatorContext,
    ) -> dict[str, Any]: ...


__all__ = ["ServiceRuntimeDevicePort"]
