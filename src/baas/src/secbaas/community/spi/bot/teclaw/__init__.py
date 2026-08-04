"""TeClaw bot plugin SPI — Protocol and types for TeClaw device lifecycle."""

from ._protocols import TeClawBotPlugin
from ._types import (
    BotCreateResult,
    BotDestroyResult,
    BotInfo,
    BotRestartResult,
    BotUpdateResult,
)

__all__ = [
    "TeClawBotPlugin",
    "BotCreateResult",
    "BotDestroyResult",
    "BotInfo",
    "BotRestartResult",
    "BotUpdateResult",
]