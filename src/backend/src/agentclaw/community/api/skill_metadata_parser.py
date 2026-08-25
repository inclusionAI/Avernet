"""Service API for canonical ``SKILL.md`` metadata parsing."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skill_center.skill_metadata import (
    SkillMetadataParserProtocol as CoreSkillMetadataParserProtocol,
)


@runtime_checkable
class SkillMetadataParserProtocol(CoreSkillMetadataParserProtocol, Protocol):
    """Adapter-facing alias of the core-owned parser contract."""
