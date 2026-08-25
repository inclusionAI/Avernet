"""Service API for canonical ``SKILL.md`` metadata parsing."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.skill_metadata import (
    SkillManifestValidationResult,
    SkillMetadata,
)


@runtime_checkable
class SkillMetadataParserProtocol(Protocol):
    """Parse the authoritative name and description from one manifest."""

    @staticmethod
    def parse_skill_markdown(
        content: str | bytes, *, path: str = "SKILL.md"
    ) -> SkillMetadata: ...

    @staticmethod
    def validate_skill_markdown(
        content: str | bytes, *, path: str = "SKILL.md"
    ) -> SkillManifestValidationResult: ...
