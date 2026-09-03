"""Service API for Bot Skill queries — one seam, no activation writes."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillQueryServiceProtocol(Protocol):
    """Answer questions about a Bot's skills. No activation writes.

    Listing and detail answer ``active`` from Installation after the reader's
    flush, so a SkillSet-bridged skill is visible without any prior write.
    ``replace_parameters`` is the one non-read, kept here so routers keep a
    single seam.
    """

    @abstractmethod
    def list_bot_skills(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        page: int,
        page_size: int,
        active: bool | None,
        keyword: str | None,
        source: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]: ...

    @abstractmethod
    def get_local_skill(self, *, skill_id: str, actor_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def get_readme_by_skill(self, *, skill_id: str, actor_id: str) -> str: ...

    @abstractmethod
    def get_skill(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    def resolve_legacy_skill_id(
        self,
        *,
        skill_reference: str,
        source_path: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> str: ...

    @abstractmethod
    async def get_content(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> str: ...

    @abstractmethod
    async def get_readme_by_skill(self, *, skill_id: str, actor_id: str) -> str: ...

    @abstractmethod
    async def get_parameters(
        self, *, skill_id: str, bot_id: str, owner_id: str, user_id: str
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def replace_parameters(
        self,
        *,
        skill_id: str,
        bot_id: str,
        owner_id: str,
        user_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]: ...
