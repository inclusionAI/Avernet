"""Service API for immutable Published Version and consumable reads."""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import Protocol, TypedDict, runtime_checkable


class PublishedSkillVersionRecord(TypedDict):
    version: int
    sc_version_number: str
    name: str
    description: str | None
    mcp_dependencies: list[str]
    published_at: datetime


class PublishedSkillFileItemRecord(TypedDict):
    path: str
    size: int


class PublishedSkillFileTreeRecord(TypedDict):
    version: int
    files: list[PublishedSkillFileItemRecord]


class PublishedSkillFileContentRecord(TypedDict):
    version: int
    path: str
    content: str


class PublishedSkillVersionSummaryRecord(TypedDict):
    version: int
    sc_version_number: str
    published_at: datetime


class ConsumableSpaceSkillSummaryRecord(TypedDict):
    skill_id: str
    name: str
    description: str | None
    latest_published_version: PublishedSkillVersionSummaryRecord


@runtime_checkable
class SpaceSkillVersionQueryServiceProtocol(Protocol):
    @abstractmethod
    def list_versions(
        self, *, space_id: int, skill_id: int, actor_id: str, page: int, page_size: int
    ) -> tuple[int, list[PublishedSkillVersionRecord]]: ...

    @abstractmethod
    def get_version(
        self, *, space_id: int, skill_id: int, version: int, actor_id: str
    ) -> PublishedSkillVersionRecord: ...

    @abstractmethod
    def get_version_file_tree(
        self, *, space_id: int, skill_id: int, version: int, actor_id: str
    ) -> PublishedSkillFileTreeRecord: ...

    @abstractmethod
    def read_version_file(
        self,
        *,
        space_id: int,
        skill_id: int,
        version: int,
        actor_id: str,
        path: str,
    ) -> PublishedSkillFileContentRecord: ...

    @abstractmethod
    def list_consumable(
        self,
        *,
        space_id: int,
        actor_id: str,
        keyword: str | None,
        page: int,
        page_size: int,
    ) -> tuple[int, list[ConsumableSpaceSkillSummaryRecord]]: ...
