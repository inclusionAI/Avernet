"""Service API contract for Space Skill Draft application commands."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SpaceSkillCreationOutcome:
    skill_id: int
    created: bool


@dataclass(frozen=True, slots=True)
class DraftFileItem:
    path: str
    size: int


@dataclass(frozen=True, slots=True)
class DraftFileTree:
    revision_id: str
    files: tuple[DraftFileItem, ...]


@dataclass(frozen=True, slots=True)
class DraftFileContent:
    path: str
    content: str
    revision_id: str


@dataclass(frozen=True, slots=True)
class DraftMutationResult:
    target_version: int
    status: str
    revision_id: str
    name: str
    description: str
    source_kind: str
    source_repo_url: str | None
    source_branch: str | None
    source_commit_sha: str | None
    source_subdir: str | None


@dataclass(frozen=True, slots=True)
class DraftDeleteOutcome:
    changed: bool
    deleted_scope: str


@runtime_checkable
class SpaceSkillApplicationServiceProtocol(Protocol):
    @abstractmethod
    def create_from_folder(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        files: Sequence[tuple[str, bytes]],
    ) -> SpaceSkillCreationOutcome: ...

    @abstractmethod
    def get_draft_file_tree(
        self, *, space_id: int, skill_id: int, actor_id: str
    ) -> DraftFileTree: ...

    @abstractmethod
    def read_draft_file(
        self, *, space_id: int, skill_id: int, actor_id: str, path: str
    ) -> DraftFileContent: ...

    @abstractmethod
    def save_draft_file(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        path: str,
        content: str,
        expected_revision_id: str,
        fencing_token: int | None,
    ) -> DraftMutationResult: ...

    @abstractmethod
    def refresh_draft_from_git(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
    ) -> DraftMutationResult: ...

    @abstractmethod
    def delete_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        expected_revision_id: str,
        fencing_token: int | None,
    ) -> DraftDeleteOutcome: ...

    @abstractmethod
    def create_upgrade_draft(
        self,
        *,
        space_id: int,
        skill_id: int,
        actor_id: str,
        request_id: str,
    ) -> DraftMutationResult: ...

    @abstractmethod
    def copy_published_version(
        self,
        *,
        space_id: int,
        skill_id: int,
        version_ordinal: int,
        actor_id: str,
        request_id: str,
    ) -> SpaceSkillCreationOutcome: ...

    @abstractmethod
    def create_from_git(
        self,
        *,
        space_id: int,
        actor_id: str,
        request_id: str,
        git_url: str,
        branch: str | None,
        subdir: str | None,
    ) -> SpaceSkillCreationOutcome: ...
