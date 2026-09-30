"""Domain contract and value objects for immutable Draft revisions."""

from __future__ import annotations

import re
from string import Formatter
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable
from uuid import UUID

from agentclaw.community.core.skill_center.skill_package import (
    ValidatedSkillPackage,
)


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
_LOCATOR = re.compile(
    r"^draft://(?P<skill_uuid>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})/v(?P<target_version>[1-9][0-9]*)/"
    r"(?P<revision_id>[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
)


class DraftContentStoreErrorCode(str, Enum):
    INVALID_IDENTITY = "INVALID_IDENTITY"
    INVALID_LOCATOR = "INVALID_LOCATOR"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    WRITE_FAILED = "WRITE_FAILED"
    READ_FAILED = "READ_FAILED"
    NOT_FOUND = "NOT_FOUND"
    DELETE_FAILED = "DELETE_FAILED"
    CONTENT_CONFLICT = "CONTENT_CONFLICT"
    CORRUPT_CONTENT = "CORRUPT_CONTENT"


class DraftContentStoreError(RuntimeError):
    """Stable failure returned by every Draft content-store implementation."""

    def __init__(self, code: DraftContentStoreErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DraftContentStoreConfig:
    """Immutable Draft ZIP key prefix; one ``{env}`` placeholder is required."""

    base_prefix_template: str = (
        "aidesktop/aidesktop_{env}/bolt_shared/skills-upload/space-drafts"
    )

    def __post_init__(self) -> None:
        template = self.base_prefix_template
        if not isinstance(template, str):
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix template must be a string",
            )
        try:
            parsed = list(Formatter().parse(template))
        except ValueError as exc:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix template is malformed",
            ) from exc
        fields = [
            (field_name, format_spec, conversion)
            for _literal, field_name, format_spec, conversion in parsed
            if field_name is not None
        ]
        if fields != [("env", "", None)]:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix template must contain one {env}",
            )
        rendered = template.format(env="validation")
        if (
            not rendered
            or rendered.startswith(("/", "\\"))
            or "\\" in rendered
            or any(
                part in {"", ".", ".."} or _SAFE_SEGMENT.fullmatch(part) is None
                for part in rendered.split("/")
            )
        ):
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_CONFIGURATION,
                "Draft content base prefix must be a safe relative path",
            )


def _canonical_uuid(value: str, *, field: str) -> str:
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise DraftContentStoreError(
            DraftContentStoreErrorCode.INVALID_IDENTITY,
            f"{field} must be a UUIDv4",
        ) from exc
    if parsed.version != 4:
        raise DraftContentStoreError(
            DraftContentStoreErrorCode.INVALID_IDENTITY,
            f"{field} must be a UUIDv4",
        )
    return str(parsed)


def _safe_segment(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or _SAFE_SEGMENT.fullmatch(value) is None
    ):
        raise DraftContentStoreError(
            DraftContentStoreErrorCode.INVALID_IDENTITY,
            f"{field} must be one safe path segment",
        )
    return value


@dataclass(frozen=True, slots=True)
class DraftRevisionIdentity:
    """Complete exact identity used to write one immutable Draft revision."""

    tenant: str
    env: str
    skill_uuid: str
    target_version: int
    revision_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant", _safe_segment(self.tenant, field="tenant"))
        object.__setattr__(self, "env", _safe_segment(self.env, field="env"))
        object.__setattr__(
            self,
            "skill_uuid",
            _canonical_uuid(self.skill_uuid, field="skill_uuid"),
        )
        if type(self.target_version) is not int or self.target_version < 1:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_IDENTITY,
                "target_version must be a positive integer",
            )
        object.__setattr__(
            self,
            "revision_id",
            _canonical_uuid(self.revision_id, field="revision_id"),
        )


@dataclass(frozen=True, slots=True)
class DraftRevisionRef(DraftRevisionIdentity):
    """A business locator plus the scope needed to resolve physical storage."""

    @property
    def locator(self) -> str:
        return f"draft://{self.skill_uuid}/v{self.target_version}/{self.revision_id}"

    @classmethod
    def from_identity(cls, identity: DraftRevisionIdentity) -> "DraftRevisionRef":
        return cls(
            tenant=identity.tenant,
            env=identity.env,
            skill_uuid=identity.skill_uuid,
            target_version=identity.target_version,
            revision_id=identity.revision_id,
        )

    @classmethod
    def from_locator(cls, *, tenant: str, env: str, locator: str) -> "DraftRevisionRef":
        match = _LOCATOR.fullmatch(locator) if isinstance(locator, str) else None
        if match is None:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_LOCATOR,
                "Draft revision locator is invalid",
            )
        try:
            return cls(
                tenant=tenant,
                env=env,
                skill_uuid=match.group("skill_uuid"),
                target_version=int(match.group("target_version")),
                revision_id=match.group("revision_id"),
            )
        except DraftContentStoreError as exc:
            raise DraftContentStoreError(
                DraftContentStoreErrorCode.INVALID_LOCATOR,
                "Draft revision locator scope is invalid",
            ) from exc


@runtime_checkable
class DraftContentStore(Protocol):
    """The complete business surface of the immutable revision store."""

    def write_revision(
        self,
        identity: DraftRevisionIdentity,
        validated_package: ValidatedSkillPackage,
    ) -> DraftRevisionRef: ...

    def read_revision(self, ref: DraftRevisionRef) -> ValidatedSkillPackage: ...

    def delete_revision(self, ref: DraftRevisionRef) -> None: ...
