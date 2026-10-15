"""Neutral API contract for managing a bot's acli market CLIs.

Re-export only; the Protocol and its DTOs live in ``core/acli_clis``.
"""
from __future__ import annotations

from agentclaw.community.core.acli_clis.errors import (
    AcliBotNotRunningError,
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliOperationFailedError,
    AcliRequestRejectedError,
    AcliRollbackFailedError,
)
from agentclaw.community.core.acli_clis.port import AcliMarketPage, AcliTool
from agentclaw.community.core.acli_clis.protocol import AcliCliServiceProtocol
from agentclaw.community.core.acli_clis.service import BotCli

__all__ = [
    "AcliBotNotRunningError",
    "AcliCliAlreadyGrantedError",
    "AcliCliNameMismatchError",
    "AcliCliNotGrantedError",
    "AcliCliServiceProtocol",
    "AcliContainerError",
    "AcliDefaultCliError",
    "AcliEngineUnsupportedError",
    "AcliGrantSyncError",
    "AcliMarketPage",
    "AcliOperationFailedError",
    "AcliRequestRejectedError",
    "AcliRollbackFailedError",
    "AcliTool",
    "BotCli",
]
