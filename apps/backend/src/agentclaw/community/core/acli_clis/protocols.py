"""The contract the HTTP adapter depends on for a bot's acli CLIs.

Also republishes the DTOs and errors that contract is typed with, so
``api/acli_cli_service.py`` can re-export everything from this one module.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.core.acli_clis.errors import (
    AcliBotNotRunningError,
    AcliCliAlreadyGrantedError,
    AcliCliNameMismatchError,
    AcliCliNotGrantedError,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliIdentityCleanupError,
    AcliOperationFailedError,
    AcliRequestRejectedError,
    AcliRollbackFailedError,
)
from agentclaw.community.core.acli_clis.port import AcliMarketPage, AcliTool


@dataclass(frozen=True, slots=True)
class BotCli:
    """One CLI a bot holds in Passport."""

    cli_code: str
    cli_name: str
    cli_desc: str
    identity_mode: str
    default: bool


@runtime_checkable
class AcliCliServiceProtocol(Protocol):
    @abstractmethod
    def list_bot_clis(self, *, bot_id: str, entity_id: str | None) -> list[BotCli]: ...

    @abstractmethod
    async def search_market(
        self,
        *,
        bot_id: str,
        entity_id: str | None,
        keyword: str,
        page_no: int,
        page_size: int,
    ) -> AcliMarketPage: ...

    @abstractmethod
    async def add(
        self,
        *,
        bot_id: str,
        entity_id: str | None,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> AcliTool: ...

    @abstractmethod
    async def remove(
        self,
        *,
        bot_id: str,
        entity_id: str | None,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
    ) -> None: ...


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
    "AcliIdentityCleanupError",
    "AcliMarketPage",
    "AcliOperationFailedError",
    "AcliRequestRejectedError",
    "AcliRollbackFailedError",
    "AcliTool",
    "BotCli",
]
