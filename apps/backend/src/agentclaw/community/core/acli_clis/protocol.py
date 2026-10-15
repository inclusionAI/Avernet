"""The contract the HTTP adapter depends on for a bot's acli CLIs."""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable

from agentclaw.community.core.acli_clis.port import AcliMarketPage, AcliTool
from agentclaw.community.core.acli_clis.service import BotCli


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


__all__ = ["AcliCliServiceProtocol"]
