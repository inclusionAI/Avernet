"""BotService plugin SPI — pluggable bot metadata backends."""

from ._models import BotBindingData, LogRelationPayload
from ._protocols import BotServicePlugin

__all__ = [
    "BotBindingData",
    "BotServicePlugin",
    "LogRelationPayload",
]
