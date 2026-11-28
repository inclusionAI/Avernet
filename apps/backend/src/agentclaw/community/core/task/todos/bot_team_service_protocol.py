"""Service API contract for the Bot team product facade."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.task.todos.bot_team_models import (
        BotTeamDefinition,
        BotTeamDetail,
        BotTeamPage,
        BotTeamRecord,
        BotTeamStatus,
    )


@runtime_checkable
class TodoBotTeamServiceProtocol(Protocol):
    """Manage Bot teams while preserving existing Todo template storage."""

    @abstractmethod
    async def create_bot_team(
        self,
        *,
        space_id: int,
        actor_id: str,
        definition: BotTeamDefinition,
        env: str | None = None,
    ) -> BotTeamDetail: ...

    @abstractmethod
    def list_bot_teams(
        self,
        *,
        space_id: int,
        actor_id: str,
        status: BotTeamStatus | str | None,
        page: int,
        page_size: int,
        env: str | None = None,
    ) -> BotTeamPage: ...

    @abstractmethod
    def get_bot_team(
        self,
        *,
        bot_team_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> BotTeamDetail: ...

    @abstractmethod
    async def update_bot_team(
        self,
        *,
        bot_team_id: str,
        actor_id: str,
        definition: BotTeamDefinition,
        env: str | None = None,
    ) -> BotTeamDetail: ...

    @abstractmethod
    def archive_bot_team(
        self,
        *,
        bot_team_id: str,
        actor_id: str,
        env: str | None = None,
    ) -> BotTeamRecord: ...
