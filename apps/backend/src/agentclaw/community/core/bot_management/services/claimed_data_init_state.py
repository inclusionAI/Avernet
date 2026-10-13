"""A data-init worker's Bot state view, fenced by its immutable dispatch claim."""

from typing import Any

from agentclaw.community.core.devices.device_service_protocol import DeviceServiceProtocol
from agentclaw.community.core.devices.repository.record import DataInitExecutionClaim


class DataInitClaimLostError(RuntimeError):
    """The runtime/startup/worker no longer owns this data-init execution."""


class ClaimedDataInitBotState:
    """Keep all worker state writes behind one atomic claim check.

    The unclaimed/manual path continues to use BotService directly. This view
    exposes only the two Bot operations used by the data-init execution chain.
    """

    def __init__(
        self, *, bot_service: Any, device_service: DeviceServiceProtocol,
        claim: DataInitExecutionClaim,
    ) -> None:
        self._bot_service = bot_service
        self._device_service = device_service
        self._claim = claim

    def assert_current(self, bot_id: str, owner_id: str) -> None:
        # Empty writes validate/renew without changing the Bot's state.
        self.update_bot_ext(bot_id, owner_id, {})

    def get_bot(self, bot_id: str, owner_id: str) -> dict:
        return self._bot_service.get_bot(bot_id, owner_id)

    def update_bot_ext(self, bot_id: str, owner_id: str, updates: dict) -> None:
        if not self._device_service.update_pool_data_init_ext_if_claim_matches(
            bot_id=bot_id, owner_id=owner_id, claim=self._claim, updates=updates,
        ):
            raise DataInitClaimLostError("data-init execution claim was superseded")
