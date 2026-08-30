"""Service API for immutable Published Version and consumable reads."""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class SpaceSkillVersionQueryServiceProtocol(Protocol):
    @abstractmethod
    def list_versions(
        self, *, space_id: int, skill_id: int, actor_id: str, page: int, page_size: int
    ) -> tuple[int, list[dict]]: ...

    @abstractmethod
    def get_version(
        self, *, space_id: int, skill_id: int, version: int, actor_id: str
    ) -> dict: ...

    @abstractmethod
    def get_version_file_tree(
        self, *, space_id: int, skill_id: int, version: int, actor_id: str
    ) -> dict: ...

    @abstractmethod
    def read_version_file(
        self,
        *,
        space_id: int,
        skill_id: int,
        version: int,
        actor_id: str,
        path: str,
    ) -> dict: ...

    @abstractmethod
    def list_consumable(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[dict]]: ...
