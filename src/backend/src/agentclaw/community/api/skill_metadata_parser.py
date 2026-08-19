"""Service API for the canonical SKILL.md metadata parser."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.skill_metadata import (
    SkillMetadata,
    SkillMetadataProjection,
    SkillMetadataValidationResult,
)


@runtime_checkable
class SkillMetadataParserProtocol(Protocol):
    """Read and validate name/description from a canonical SKILL.md file."""

    @staticmethod
    def parse_skill_markdown(
        content: str | bytes, *, path: str = "SKILL.md"
    ) -> SkillMetadata: ...

    @staticmethod
    def validate_skill_markdown(
        content: str | bytes, *, path: str = "SKILL.md"
    ) -> SkillMetadataValidationResult: ...

    @staticmethod
    def project_skill_markdown(
        content: str | bytes, *, path: str = "SKILL.md"
    ) -> SkillMetadataProjection: ...
