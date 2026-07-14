"""TeClaw bot plugin SPI — Protocol and types for TeClaw device lifecycle."""

from ._protocols import TeClawBotPlugin
from ._types import (
    _BotCreateResult,
    _BotDestroyResult,
    _BotInfo,
    _BotRestartResult,
    _BotUpdateResult,
)

__all__ = [
    "TeClawBotPlugin",
    "_BotCreateResult",
    "_BotDestroyResult",
    "_BotInfo",
    "_BotRestartResult",
    "_BotUpdateResult",
]
