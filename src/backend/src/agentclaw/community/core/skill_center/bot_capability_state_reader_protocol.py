"""Public Service API for reading a Bot's active capability state."""

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol as CoreBotCapabilityStateReaderProtocol,
)


@runtime_checkable
class BotCapabilityStateReaderProtocol(
    CoreBotCapabilityStateReaderProtocol, Protocol
):
    """Transport-facing contract; Core depends only on its sibling contract."""


__all__ = ["BotCapabilityStateReaderProtocol"]
