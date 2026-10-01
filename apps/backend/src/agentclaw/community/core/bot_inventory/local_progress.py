"""Bot-addressed startup progress, authorized before runtime lookup."""

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.bot_inventory.errors import BotInventoryUpstreamError
from agentclaw.community.core.bot_inventory.local_bot_workflow_service_protocol import (
    LocalBotWorkflowServiceProtocol,
)
from agentclaw.community.core.service_bot.baas_service_errors import BaasServiceError


class StartupProgressPort(Protocol):
    def get_bot_start_progress(
        self, bot_uuid: str, tenant: str = "", device_affinity: str | None = None
    ) -> dict[str, Any]: ...


@runtime_checkable
class LocalProgressServiceProtocol(Protocol):
    def get(
        self, *, bot_id: str, owner_id: str, header_space_id: str | None
    ) -> dict[str, Any]: ...


class LocalProgressService:
    def __init__(
        self, workflow: LocalBotWorkflowServiceProtocol, provider: StartupProgressPort
    ):
        self._workflow = workflow
        self._provider = provider

    def get(
        self, *, bot_id: str, owner_id: str, header_space_id: str | None
    ) -> dict[str, Any]:
        bot = self._workflow.get_bot(
            bot_id=bot_id, owner_id=owner_id, header_space_id=header_space_id
        )
        device_id = bot.get("device_id")
        if not isinstance(device_id, str) or not device_id:
            raise BotInventoryUpstreamError("desktop bot has no runtime device")
        try:
            return self._provider.get_bot_start_progress(
                bot_uuid=device_id, device_affinity=owner_id
            )
        except BaasServiceError as exc:
            raise BotInventoryUpstreamError(
                "desktop startup progress unavailable"
            ) from exc
