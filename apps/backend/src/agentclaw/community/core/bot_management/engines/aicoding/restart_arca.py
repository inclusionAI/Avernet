"""AICoding-owned old-Arca target checks and backup execution.

Command execution is delegated to an Arca transport supplied by a composition
root. Recovery state eligibility and failures that must block replacement belong
to the coding restart strategy.
"""
from __future__ import annotations

from typing import Protocol

from agentclaw.community.core.devices.models import AllocatedDevice
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.kernel.device_dto import CommandResult


class ArcaRestartCommand(Protocol):
    """Existing provider hook signature; it must not inspect coding restart state."""

    def __call__(self, *, device: AllocatedDevice, shell_cmd: str) -> CommandResult: ...


class ArcaRestartBindings(Protocol):
    """Narrow binding-read capability; no router or provider internals."""

    def get_device(self, *, binding_id: int) -> DeviceBindingRecord: ...


class RestartBackupTargetChanged(RuntimeError):
    """Coding restart cannot safely replace its captured old target."""

    reason = "target_changed"


class ArcaRestartTransport:
    """Strategy-owned transport contract over the existing provider hook.

    The unconfigured implementation deliberately blocks direct-Arca recovery.
    Community composition roots bind this unavailable form; OCB binds the
    registered Arca provider. Other engines never resolve it.
    """

    def __init__(self, execute: ArcaRestartCommand | None = None):
        self._execute = execute

    def require_available(self) -> None:
        if self._execute is None:
            raise RestartBackupTargetChanged("Arca restart transport unavailable")

    def execute(self, *, device: AllocatedDevice, shell_cmd: str) -> CommandResult:
        execute = self._execute
        if execute is None:
            raise RestartBackupTargetChanged("Arca restart transport unavailable")
        return execute(device=device, shell_cmd=shell_cmd)


class BoundArcaRestartExecutor:
    """Pin the old sandbox and verify it before each helper command/replacement."""

    def __init__(
        self, *, bindings: ArcaRestartBindings, transport: ArcaRestartTransport, binding_id: int,
        device_id: str, sandbox_id: str,
    ) -> None:
        self._bindings = bindings
        self._binding_id = binding_id
        self._device_id = device_id
        self._sandbox_id = sandbox_id
        self.verify()
        transport.require_available()
        self._transport = transport

    def verify(self) -> None:
        try:
            binding = self._bindings.get_device(binding_id=self._binding_id)
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
        return self._transport.execute(
            device=AllocatedDevice(
                device_id=self._device_id, device_provider="arca",
                device_props={"sandbox_id": self._sandbox_id},
            ),
            shell_cmd=command,
        )
