"""Internal old-sandbox execution for coding restart, not a general exec API.

Arca release and backup must address the same binding.device_props.sandbox_id.
The provider's existing execution hook owns suffix/tenant translation. Recovery
may execute on FAILED/STOPPED bindings without relaxing ordinary exec guards.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.devices.errors import RestartBackupTargetChanged
from agentclaw.community.core.devices.models import AllocatedDevice

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_service import DeviceService
    from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
    from agentclaw.community.kernel.device_dto import CommandResult


class BoundArcaRestartExecutor:
    """Pin command execution to the old sandbox, including its tenant suffix.

    Only the internal restart precondition obtains this executor. No HTTP
    route exposes it. Callers supply the existing restart-helper commands,
    never user input. verify() also runs immediately before replacement.
    """

    def __init__(
        self, *, repository: DeviceBindingRepository, provider: DeviceService,
        binding_id: int, device_id: str, sandbox_id: str,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._binding_id = binding_id
        self._device_id = device_id
        self._sandbox_id = sandbox_id

    def verify(self) -> None:
        try:
            binding = self._repository.get_by_id(self._binding_id)
        except Exception as error:
            # Preserve database failure as blocking, without leaking its text.
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
        # Never re-resolve a replacement target by logical device_id. Only the
        # captured physical identity goes to the same provider hook as normal
        # exec; that hook shares release's sandbox/tenant parsing.
        return self._provider._exec_shell_new(
            device=AllocatedDevice(
                device_id=self._device_id,
                device_provider="arca",
                device_props={"sandbox_id": self._sandbox_id},
            ),
            shell_cmd=command,
        )
