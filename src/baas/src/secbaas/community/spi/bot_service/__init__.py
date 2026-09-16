"""BotService plugin SPI — pluggable bot metadata backends."""

from ._models import (
    BotBindingData,
    CallerConnection,
    CallerConnectionData,
    CallerInstance,
    IamTokenData,
    LogRelationPayload,
    Result,
)
from ._protocols import BotServicePlugin

__all__ = [
    "BotBindingData",
    "BotServicePlugin",
    "CallerConnection",
    "CallerConnectionData",
    "CallerInstance",
    "IamTokenData",
    "LogRelationPayload",
    "Result",
]
