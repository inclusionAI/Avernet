"""Public value objects for the canonical SKILL.md metadata contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class SkillMetadata:
    """Name and description read from one ``SKILL.md`` manifest."""

    name: str
    description: str


@dataclass(frozen=True)
class SkillMetadataProjection:
    """Read model for consumers that display SKILL.md metadata."""

    name: str
    description: str

    @classmethod
    def from_metadata(cls, metadata: SkillMetadata) -> "SkillMetadataProjection":
        return cls(name=metadata.name, description=metadata.description)

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


class SkillMetadataErrorCode(str, Enum):
    """Stable validation codes for the SKILL.md metadata contract."""

    MISSING_FRONTMATTER = "missing_frontmatter"
    INVALID_FRONTMATTER = "invalid_frontmatter"
    INVALID_ENCODING = "invalid_encoding"
    INVALID_PATH = "invalid_path"
    MISSING_NAME = "missing_name"
    MISSING_DESCRIPTION = "missing_description"
    INVALID_NAME = "invalid_name"
    INVALID_DESCRIPTION = "invalid_description"
    MISSING_BODY = "missing_body"
    NAME_TOO_LONG = "name_too_long"
    DESCRIPTION_TOO_LONG = "description_too_long"


@dataclass(frozen=True)
class SkillMetadataValidationIssue:
    """One caller-facing failure to satisfy the SKILL.md metadata contract."""

    code: SkillMetadataErrorCode
    field: str | None = None


@dataclass(frozen=True)
class SkillMetadataValidationResult:
    """The parsed metadata or stable validation issues for one manifest."""

    metadata: SkillMetadata | None
    errors: tuple[SkillMetadataValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return self.metadata is not None and not self.errors


class SkillMetadataValidationError(ValueError):
    """Raised by strict readers while retaining a stable error code."""

    def __init__(self, issue: SkillMetadataValidationIssue) -> None:
        self.issue = issue
        super().__init__(issue.code.value)
