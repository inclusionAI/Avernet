"""Domain contract for immutable exact-version Skill Center content."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable
from uuid import UUID


_FORMAT_VERSION = 1
_RESERVED_NAMES = frozenset({".teamclaw-write.json", ".teamclaw-ready.json"})


class CanonicalCenterStoreErrorCode(str, Enum):
    INVALID_IDENTITY = "INVALID_IDENTITY"
    INVALID_FILE_TREE = "INVALID_FILE_TREE"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    WRITE_FAILED = "WRITE_FAILED"
    READ_FAILED = "READ_FAILED"
    NOT_READY = "NOT_READY"
    CONTENT_CONFLICT = "CONTENT_CONFLICT"
    CORRUPT_CONTENT = "CORRUPT_CONTENT"


class CanonicalCenterStoreError(RuntimeError):
    def __init__(self, code: CanonicalCenterStoreErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def _safe_segment(value: str, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or len(value) > 128
    ):
        raise CanonicalCenterStoreError(
            CanonicalCenterStoreErrorCode.INVALID_IDENTITY,
            f"{field} must be a safe non-empty path segment",
        )
    return value


def _safe_file_path(raw: str) -> str:
    if (
        not isinstance(raw, str)
        or not raw
        or raw != raw.strip()
        or raw.startswith("/")
        or "\\" in raw
        or "\x00" in raw
        or len(raw) > 512
    ):
        raise CanonicalCenterStoreError(
            CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
            f"unsafe canonical file path: {raw!r}",
        )
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise CanonicalCenterStoreError(
            CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
            f"unsafe canonical file path: {raw!r}",
        )
    normalized = path.as_posix()
    if normalized in _RESERVED_NAMES or normalized.startswith(".teamclaw-"):
        raise CanonicalCenterStoreError(
            CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
            f"reserved canonical file path: {raw!r}",
        )
    return normalized


@dataclass(frozen=True, slots=True)
class CanonicalCenterVersionIdentity:
    skill_uuid: str
    sc_version_number: str

    def __post_init__(self) -> None:
        try:
            parsed = UUID(self.skill_uuid)
        except (ValueError, AttributeError) as error:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_IDENTITY,
                "skill_uuid must be a UUIDv4",
            ) from error
        if parsed.version != 4 or str(parsed) != self.skill_uuid:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_IDENTITY,
                "skill_uuid must be a canonical UUIDv4",
            )
        version = _safe_segment(
            self.sc_version_number,
            field="sc_version_number",
        )
        if version.lower() in {"latest", "current"}:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_IDENTITY,
                "sc_version_number must identify an exact version",
            )

    @property
    def locator(self) -> str:
        return f"center-version://{self.skill_uuid}/{self.sc_version_number}"


@dataclass(frozen=True, slots=True)
class CanonicalCenterVersionRef:
    identity: CanonicalCenterVersionIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CanonicalCenterVersionIdentity):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_IDENTITY,
                "canonical version ref requires a validated exact identity",
            )

    @property
    def locator(self) -> str:
        return self.identity.locator


@dataclass(frozen=True, slots=True)
class CanonicalCenterFile:
    path: str
    content: bytes
    sha256: str

    def __post_init__(self) -> None:
        path = _safe_file_path(self.path)
        if not isinstance(self.content, bytes):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
                f"canonical file content must be bytes: {path}",
            )
        expected = hashlib.sha256(self.content).hexdigest()
        if self.sha256 != expected:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
                f"canonical file digest does not match content: {path}",
            )
        object.__setattr__(self, "path", path)


@dataclass(frozen=True, slots=True)
class CanonicalCenterVersion:
    identity: CanonicalCenterVersionIdentity
    files: tuple[CanonicalCenterFile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CanonicalCenterVersionIdentity):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_IDENTITY,
                "canonical version requires a validated exact identity",
            )
        if not isinstance(self.files, tuple) or any(
            not isinstance(item, CanonicalCenterFile) for item in self.files
        ):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
                "canonical version files must be validated file values",
            )
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
                "canonical version contains duplicate file paths",
            )
        by_path = {item.path: item for item in self.files}
        if "SKILL.md" not in by_path or not by_path["SKILL.md"].content:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
                "canonical exact version requires a non-empty root SKILL.md",
            )
        object.__setattr__(
            self,
            "files",
            tuple(sorted(self.files, key=lambda item: item.path)),
        )

    @classmethod
    def from_files(
        cls,
        identity: CanonicalCenterVersionIdentity,
        files: Mapping[str, bytes],
    ) -> "CanonicalCenterVersion":
        normalized: dict[str, bytes] = {}
        for raw_path, content in files.items():
            path = _safe_file_path(raw_path)
            if path in normalized or not isinstance(content, bytes):
                raise CanonicalCenterStoreError(
                    CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
                    f"invalid or duplicate canonical file: {raw_path!r}",
                )
            normalized[path] = content
        if "SKILL.md" not in normalized or not normalized["SKILL.md"]:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_FILE_TREE,
                "canonical exact version requires a non-empty root SKILL.md",
            )
        entries = tuple(
            CanonicalCenterFile(
                path=path,
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
            )
            for path, content in sorted(normalized.items())
        )
        return cls(identity=identity, files=entries)

    @property
    def skill_md(self) -> bytes:
        return next(item.content for item in self.files if item.path == "SKILL.md")

    @property
    def manifest(self) -> bytes:
        value = {
            "format_version": _FORMAT_VERSION,
            "skill_uuid": self.identity.skill_uuid,
            "sc_version_number": self.identity.sc_version_number,
            "files": [
                {"path": item.path, "sha256": item.sha256, "size": len(item.content)}
                for item in self.files
            ],
        }
        return json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.manifest).hexdigest()

    @property
    def file_map(self) -> Mapping[str, bytes]:
        return MappingProxyType({item.path: item.content for item in self.files})


@dataclass(frozen=True, slots=True)
class CanonicalCenterStoreConfig:
    env: str = "prod"
    base_prefix_template: str = (
        "aidesktop/aidesktop_{env}/bolt_shared/skills-center"
    )

    def __post_init__(self) -> None:
        try:
            env = _safe_segment(self.env, field="env")
        except CanonicalCenterStoreError as error:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION,
                str(error),
            ) from error
        template = self.base_prefix_template
        if not isinstance(template, str) or template.count("{env}") != 1:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION,
                "base_prefix_template must contain exactly one {env} placeholder",
            )
        try:
            rendered = template.format(env=env)
        except (KeyError, ValueError) as error:
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION,
                "base_prefix_template contains unsupported placeholders",
            ) from error
        if (
            rendered.startswith("/")
            or "\\" in rendered
            or "//" in rendered
            or any(part in {"", ".", ".."} for part in PurePosixPath(rendered).parts)
        ):
            raise CanonicalCenterStoreError(
                CanonicalCenterStoreErrorCode.INVALID_CONFIGURATION,
                "base_prefix_template must render a safe relative object prefix",
            )

    @property
    def base_prefix(self) -> str:
        return self.base_prefix_template.format(env=self.env).rstrip("/")


@runtime_checkable
class CanonicalCenterVersionStore(Protocol):
    def write_version(
        self, version: CanonicalCenterVersion
    ) -> CanonicalCenterVersionRef: ...

    def read_version(
        self, ref: CanonicalCenterVersionRef
    ) -> CanonicalCenterVersion: ...

    def verify_version(self, ref: CanonicalCenterVersionRef) -> bool: ...
