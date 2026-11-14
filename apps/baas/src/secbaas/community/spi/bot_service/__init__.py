"""BotService plugin SPI — pluggable bot metadata backends."""

from ._models import (
    CALLER_CREDENTIAL_TYPE_AGENTPASS,
    CALLER_CREDENTIAL_TYPE_IAM_TOKEN,
    BotBindingData,
    CallerConnection,
    CallerConnectionData,
    CallerExecutionCredential,
    CallerInstance,
    IamTokenData,
    LogRelationPayload,
    Result,
)
from ._protocols import BotServicePlugin

__all__ = [
    "CALLER_CREDENTIAL_TYPE_AGENTPASS",
    "CALLER_CREDENTIAL_TYPE_IAM_TOKEN",
    "BotBindingData",
    "BotServicePlugin",
    "CallerConnection",
    "CallerConnectionData",
    "CallerExecutionCredential",
    "CallerInstance",
    "IamTokenData",
    "LogRelationPayload",
    "Result",
]
