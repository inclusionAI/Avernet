"""Service API for shared Center references used by Direct and SkillSet Apply."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
import json

from agentclaw.community.core.skill_center.materialization_contract import (
    ValidatedExactSkillPackage,
)
from agentclaw.community.core.skill_center.mcp_dependency_scope import (
    mcp_dependency_codes,
)
from agentclaw.community.core.skill_center.version_resolution_contract import (
    PublishedSkillVersion,
)


@dataclass(frozen=True, slots=True)
class SkillAccessScope:
    """Trusted Apply scope; tenant comes from the existing tenant context."""

    env: str
    bot_id: str
    owner_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if not all((self.env, self.bot_id, self.owner_id, self.actor_id)):
            raise ValueError("Skill reference scope requires env, Bot, owner and actor")


@dataclass(frozen=True, slots=True)
class ReadySharedSkill:
    skill_id: str
    skill_uuid: str
    name: str
    version: PublishedSkillVersion

    @property
    def mcp_dependencies(self) -> frozenset[str]:
        return frozenset(mcp_dependency_codes(self.version.mcp_dependencies))


@dataclass(frozen=True, slots=True)
class ReadyRepoSkill:
    """An existing governed Repo asset; dependency NULL follows legacy decoding."""

    skill_id: str
    name: str
    git_path: str
    mcp_dependencies: frozenset[str]


@dataclass(frozen=True, slots=True)
class PublicSkillPreview:
    """Private plan value. Content, digests and exact identity are never report DTOs."""

    skill_code: str
    skill_uuid: str
    name: str
    sc_skill_id: int
    sc_version_number: str
    sc_version_id: int
    content: ValidatedExactSkillPackage

    @property
    def mcp_dependencies(self) -> frozenset[str]:
        metadata = json.loads(self.content.metadata_json)
        dependencies = (
            metadata.get("mcp_dependencies") if isinstance(metadata, dict) else None
        )
        if not isinstance(dependencies, list):
            raise ValueError("Public package has unknown MCP dependencies")
        return frozenset(mcp_dependency_codes(dependencies))


@runtime_checkable
class SkillReferenceServiceProtocol(Protocol):
    """Reads have no management writes; prepare is the explicit publication boundary."""

    def resolve_shared_skill_id(
        self, *, scope: SkillAccessScope, skill_id: str, expected_name: str
    ) -> ReadySharedSkill | ReadyRepoSkill: ...

    def resolve_center_uuid(
        self, *, scope: SkillAccessScope, skill_uuid: str, expected_name: str
    ) -> ReadySharedSkill: ...

    def preview_public_reference(
        self, *, scope: SkillAccessScope, skill_code: str, expected_name: str
    ) -> ReadySharedSkill | PublicSkillPreview: ...

    def prepare_public_reference(
        self, *, scope: SkillAccessScope, preview: PublicSkillPreview
    ) -> ReadySharedSkill: ...


__all__ = [
    "SkillAccessScope",
    "ReadySharedSkill",
    "ReadyRepoSkill",
    "PublicSkillPreview",
    "SkillReferenceServiceProtocol",
]
