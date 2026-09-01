"""Stable application contract for exact Center Version publication."""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from agentclaw.community.plugin_api.skill_center_gateway import (
    SkillCenterReadScope,
)


class SkillVersionMaterializationError(RuntimeError):
    """An exact Version failed a Ready Gate and remains non-consumable."""

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class SkillVersionMaterializationRequest:
    """Address one already-created MATERIALIZING Version."""

    env: str
    skill_id: int
    skill_version_id: int
    scope: SkillCenterReadScope
    team_id: str | None = None

    def __post_init__(self) -> None:
        if not self.env or self.skill_id < 1 or self.skill_version_id < 1:
            raise ValueError("materialization requires an exact Version identity")
        if self.scope is SkillCenterReadScope.TEAM:
            if not self.team_id:
                raise ValueError("TEAM materialization requires team_id")
        elif self.team_id is not None:
            raise ValueError("PUBLIC materialization must not carry team_id")


@dataclass(frozen=True, slots=True)
class MaterializingSkillVersion:
    """Persisted facts needed to materialize or verify one exact Version."""

    skill_version_id: int
    skill_id: int
    version_ordinal: int
    status: Literal["MATERIALIZING", "PUBLISHED"]
    skill_uuid: str
    skill_code: str
    sc_version_number: str
    sc_skill_id: int
    sc_version_id: int
    name: str
    description: str | None
    metadata_json: str | None
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class PublishedMaterializedSkillVersion:
    """The stable Version-Published application seam."""

    skill_version_id: int
    skill_id: int
    version_ordinal: int
    status: Literal["PUBLISHED"]
    skill_uuid: str
    sc_version_number: str
    sc_skill_id: int
    sc_version_id: int
    name: str
    description: str | None
    metadata_json: str
    published_at: datetime


@runtime_checkable
class SkillVersionMaterializerProtocol(Protocol):
    """Consumer-first seam reused by future Publication and Reference flows."""

    @abstractmethod
    def materialize(
        self, request: SkillVersionMaterializationRequest
    ) -> PublishedMaterializedSkillVersion: ...


__all__ = [
    "MaterializingSkillVersion",
    "PublishedMaterializedSkillVersion",
    "SkillVersionMaterializationError",
    "SkillVersionMaterializationRequest",
    "SkillVersionMaterializerProtocol",
]
