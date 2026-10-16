"""Neutral API contract for managing a bot's acli market CLIs.

Re-export only; the Protocol, its DTOs and errors come from
``core/acli_clis/protocols.py``.
"""
from __future__ import annotations

from agentclaw.community.core.acli_clis.protocols import (
    AcliBotNotRunningError,
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliCliServiceProtocol,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketNotConfiguredError,
    AcliMarketUnavailableError,
    AcliMarketPage,
    AcliOperationFailedError,
    AcliRequestRejectedError,
    AcliRollbackFailedError,
    AcliTool,
    BotCli,
)

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
    "AcliMarketNotConfiguredError",
    "AcliMarketUnavailableError",
    "AcliMarketPage",
    "AcliOperationFailedError",
    "AcliRequestRejectedError",
    "AcliRollbackFailedError",
    "AcliTool",
    "BotCli",
]
