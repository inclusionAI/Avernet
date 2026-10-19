"""AICoding-owned old-Arca target checks and backup execution.

Only the device transport is delegated. Recovery state eligibility and failures
that must block replacement belong to the coding restart strategy.
"""
from __future__ import annotations

from typing import Callable, Protocol

from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.kernel.device_dto import CommandResult


class ArcaRestartDevices(Protocol):
    """Device capabilities consumed by the coding strategy, without router internals."""

    def get_device(self, *, binding_id: int) -> DeviceBindingRecord: ...

    def bind_device_executor(self, *, device: AllocatedDevice) -> Callable[[str], CommandResult]: ...


class RestartBackupTargetChanged(RuntimeError):
    """Coding restart cannot safely replace its captured old target."""

    reason = "target_changed"


class BoundArcaRestartExecutor:
    """Pin the old sandbox and verify it before each helper command/replacement."""

    def __init__(
        self, *, devices: ArcaRestartDevices, binding_id: int,
        device_id: str, sandbox_id: str,
    ) -> None:
        self._devices = devices
        self._binding_id = binding_id
        self._device_id = device_id
        self._sandbox_id = sandbox_id
        self.verify()
        try:
            self._execute = devices.bind_device_executor(device=AllocatedDevice(
                device_id=device_id, device_provider="arca",
                device_props={"sandbox_id": sandbox_id},
            ))
        except DeviceServiceError as error:
            raise RestartBackupTargetChanged("Arca restart provider unavailable") from error

    def verify(self) -> None:
        try:
            binding = self._devices.get_device(binding_id=self._binding_id)
        except Exception as error:
            raise RestartBackupTargetChanged("Cannot read restart binding") from error
        if (
            binding is None
            or not self._sandbox_id
            or binding.device_id != self._device_id
            or binding.device_provider != "arca"
            or (binding.device_props or {}).get("sandbox_id") != self._sandbox_id
            or binding.status not in {"ACTIVE", "PENDING", "FAILED", "STOPPED"}
        ):
            raise RestartBackupTargetChanged("Arca restart target changed")

    def execute(self, command: str) -> CommandResult:
        self.verify()
        return self._execute(command)
