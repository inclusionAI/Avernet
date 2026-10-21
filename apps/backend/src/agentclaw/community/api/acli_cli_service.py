"""Neutral API contract for managing a bot's acli market CLIs.

Re-export only; the Protocol, its DTOs and errors come from
``core/acli_clis/protocols.py``.
"""
from __future__ import annotations

from agentclaw.community.core.acli_clis.protocols import (
    AcliBotNotRunningError,
    AcliCliNameMismatchError,
    AcliCliServiceProtocol,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketNotConfiguredError,
    AcliMarketPage,
    AcliMarketUnavailableError,
    AcliOperation,
    AcliOperationCancelledError,
    AcliOperationFailedError,
    AcliOperationInProgressError,
    AcliOperationNotActiveError,
    AcliOperationNotFoundError,
    AcliOutcome,
    AcliRequestRejectedError,
    AcliStageNotAllowedError,
    AcliTool,
    BotCli,
    AcliAction,
    OperationKind,
    OperationStatus,
    OperationStep,
)

__all__ = [
    "AcliBotNotRunningError",
    "AcliCliNameMismatchError",
    "AcliCliServiceProtocol",
    "AcliContainerError",
    "AcliDefaultCliError",
    "AcliEngineOutdatedError",
    "AcliEngineUnsupportedError",
    "AcliGrantSyncError",
    "AcliMarketNotConfiguredError",
    "AcliMarketPage",
    "AcliMarketUnavailableError",
    "AcliOperation",
    "AcliOperationCancelledError",
    "AcliOperationFailedError",
    "AcliOperationInProgressError",
    "AcliOperationNotActiveError",
    "AcliOperationNotFoundError",
    "AcliOutcome",
    "AcliRequestRejectedError",
    "AcliStageNotAllowedError",
    "AcliTool",
    "BotCli",
    "AcliAction",
    "OperationKind",
    "OperationStatus",
    "OperationStep",
]
