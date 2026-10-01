"""TeClaw bot plugin SPI — Protocol and types for TeClaw device lifecycle."""

from ._protocols import TeClawBotPlugin
from ._types import (
    BotAsyncTaskResult,
    BotCreateResult,
    BotDestroyResult,
    BotInfo,
    BotRestartResult,
    BotUpdateResult,
)

__all__ = [
    "TeClawBotPlugin",
    "BotAsyncTaskResult",
    "BotCreateResult",
    "BotDestroyResult",
    "BotInfo",
    "BotRestartResult",
    "BotUpdateResult",
]
