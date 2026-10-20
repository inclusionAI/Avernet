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
    AcliCliNameMismatchError,
    AcliContainerError,
    AcliDefaultCliError,
    AcliEngineOutdatedError,
    AcliEngineUnsupportedError,
    AcliGrantSyncError,
    AcliMarketNotConfiguredError,
    AcliMarketUnavailableError,
    AcliOperationCancelledError,
    AcliOperationFailedError,
    AcliOperationInProgressError,
    AcliOperationNotActiveError,
    AcliOperationNotFoundError,
    AcliRequestRejectedError,
    AcliStageNotAllowedError,
)
from agentclaw.community.core.acli_clis.operations import AcliOperation
from agentclaw.community.core.acli_clis.port import AcliMarketPage, AcliTool


@dataclass(frozen=True, slots=True)
class AcliOutcome:
    """What an add, remove or cancel request is answered with.

    ``operation.running`` means the request stopped waiting (``async``, or the
    30 s were up): answer ``202`` with it. Otherwise it has ended in success —
    a failure is raised instead — and ``tool`` is what an add installed.
    """

    operation: AcliOperation
    tool: AcliTool | None = None


@dataclass(frozen=True, slots=True)
class BotCli:
    """One CLI a bot holds in Passport."""

    #: The tool's code in Passport, its acli market name, e.g. ``deepinsight-cli``.
    cli_code: str
    #: Display name stored with the grant; the code when none was stored.
    cli_name: str
    #: Description stored with the grant; may be empty.
    cli_desc: str
    #: ``owner`` or ``caller``: whether the tool runs as the bot's owner or as
    #: the user calling the bot. The per-tool override row wins over the value
    #: stored in Passport.
    identity_mode: str
    #: ``True`` only for tools from the bot's default profile in
    #: ``cli-capabilities.yaml``. Computed on read, never stored; a default
    #: tool is re-granted at every start and cannot be removed.
    default: bool


@runtime_checkable
class AcliCliServiceProtocol(Protocol):
    @abstractmethod
    async def list_bot_clis(
        self, *, bot_id: str, entity_id: str, actor_id: str, stage: str | None = None
    ) -> list[BotCli]: ...

    @abstractmethod
    async def search_market(
        self, *, keyword: str, page_no: int, page_size: int
    ) -> AcliMarketPage:
        """One page of published acli market tools; not tied to any bot."""
        ...

    @abstractmethod
    async def get_operation(
        self, *, bot_id: str, entity_id: str, actor_id: str, operation_id: str
    ) -> AcliOperation: ...

    @abstractmethod
    async def live_operations(
        self, *, bot_id: str, entity_id: str, actor_id: str, stage: str | None = None
    ) -> list[AcliOperation]: ...

    @abstractmethod
    async def add(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
        stage: str | None = None,
        run_async: bool = False,
    ) -> AcliOutcome: ...

    @abstractmethod
    async def remove(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        lock_epoch: int | None,
        stage: str | None = None,
        run_async: bool = False,
    ) -> AcliOutcome: ...

    @abstractmethod
    async def cancel(
        self,
        *,
        bot_id: str,
        entity_id: str,
        actor_id: str,
        cli_code: str,
        action: str,
        lock_epoch: int | None,
        stage: str | None = None,
        run_async: bool = False,
    ) -> AcliOutcome: ...


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
]
