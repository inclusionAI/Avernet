"""Value objects for canonical ``SKILL.md`` metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SkillManifestErrorCode(str, Enum):
    """Stable machine-readable failures for the manifest contract."""

    MISSING_FRONTMATTER = "MISSING_FRONTMATTER"
    INVALID_FRONTMATTER = "INVALID_FRONTMATTER"
    INVALID_ENCODING = "INVALID_ENCODING"
    INVALID_PATH = "INVALID_PATH"
    MISSING_NAME = "MISSING_NAME"
    MISSING_DESCRIPTION = "MISSING_DESCRIPTION"
    INVALID_NAME_TYPE = "INVALID_NAME_TYPE"
    INVALID_DESCRIPTION_TYPE = "INVALID_DESCRIPTION_TYPE"
    EMPTY_NAME = "EMPTY_NAME"
    EMPTY_DESCRIPTION = "EMPTY_DESCRIPTION"
    NAME_TOO_LONG = "NAME_TOO_LONG"
    DESCRIPTION_TOO_LONG = "DESCRIPTION_TOO_LONG"
    NAME_DIRECTORY_MISMATCH = "NAME_DIRECTORY_MISMATCH"
    SKILL_FILE_READ_ERROR = "SKILL_FILE_READ_ERROR"
    INVALID_CONFIG = "INVALID_CONFIG"


class SkillManifestError(ValueError):
    """Strict parse failure carrying a stable code and optional field."""

    def __init__(
        self,
        code: str | SkillManifestErrorCode,
        message: str,
        field: str | None = None,
    ) -> None:
        self.code = SkillManifestErrorCode(code)
        self.field = field
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Authoritative display metadata read only from ``SKILL.md``."""

    name: str
    description: str

    def to_dict(self) -> dict[str, str]:
        """Project the value into legacy dictionary consumers."""
        return {"name": self.name, "description": self.description}


@dataclass(frozen=True, slots=True)
class SkillManifestValidationIssue:
    """One stable manifest validation failure."""

    code: SkillManifestErrorCode
    field: str | None = None


@dataclass(frozen=True, slots=True)
class SkillManifestValidationResult:
    """Non-throwing result for callers that need to render validation."""

    metadata: SkillMetadata | None
    errors: tuple[SkillManifestValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return self.metadata is not None and not self.errors
