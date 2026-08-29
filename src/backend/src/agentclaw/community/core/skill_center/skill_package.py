"""Pure normalization and validation for complete Skill packages."""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass

from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_metadata import (
    SkillManifestError,
    SkillManifestErrorCode,
    SkillMetadataParserProtocol,
)


MAX_COMPRESSED_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 500
MAX_PATH_LENGTH = 256

_SKILL_NAME = re.compile(r"^[A-Za-z0-9-]+$")
_RESERVED_SKILL_NAMES = frozenset({"skills-center", "skills-local", "skills-repo"})
_CANONICAL_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


class SkillPackageInvalidError(ValueError):
    """A package is malformed or violates the shared Skill layout contract."""

    def __init__(self, reason: str = "invalid_package") -> None:
        self.reason = reason
        super().__init__(reason)


class SkillPackageTooLargeError(ValueError):
    """A package exceeds a documented compressed or expanded limit."""


@dataclass(frozen=True, slots=True)
class ValidatedSkillPackage:
    """Canonical, immutable contents of one complete Skill package."""

    name: str
    description: str
    files: tuple[tuple[str, bytes], ...]
    canonical_zip: bytes


class SkillPackageValidator:
    """Validate ZIP or directory input and return one canonical value object."""

    def __init__(self, metadata_parser: SkillMetadataParserProtocol) -> None:
        self._metadata_parser = metadata_parser

    def validate_zip(self, package: bytes) -> ValidatedSkillPackage:
        if len(package) > MAX_COMPRESSED_BYTES:
            raise SkillPackageTooLargeError()
        try:
            archive = zipfile.ZipFile(io.BytesIO(package))
        except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise SkillPackageInvalidError("invalid_zip") from exc

        entries: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        total = 0
        with archive:
            for info in archive.infolist():
                file_kind = (info.external_attr >> 16) & 0o170000
                if info.is_dir():
                    if file_kind not in (0, 0o040000):
                        raise SkillPackageInvalidError("unsafe_file_path")
                    continue
                normalized_path = self._normalize_path(info.filename)
                if file_kind not in (0, 0o100000):
                    raise SkillPackageInvalidError("unsafe_file_path")
                if self._is_ignored_path(normalized_path):
                    continue
                if normalized_path in seen:
                    raise SkillPackageInvalidError("duplicate_file_path")
                if info.file_size > MAX_FILE_BYTES:
                    raise SkillPackageTooLargeError()
                seen.add(normalized_path)
                total += info.file_size
                if len(seen) > MAX_FILES or total > MAX_EXPANDED_BYTES:
                    raise SkillPackageTooLargeError()
                try:
                    content = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise SkillPackageInvalidError("unreadable_archive") from exc
                if len(content) != info.file_size:
                    raise SkillPackageInvalidError("unreadable_archive")
                entries.append((normalized_path, content))
        return self._validate_entries(entries)

    def validate_directory(
        self, files: Sequence[tuple[str, bytes]]
    ) -> ValidatedSkillPackage:
        """Validate browser directory input through the canonical ZIP seam."""
        return self.validate_zip(self.pack_directory(files))

    def pack_directory(self, files: Sequence[tuple[str, bytes]]) -> bytes:
        """Safely encode browser directory input before lifecycle authorization.

        Local upload historically rejects malformed multipart paths and sizes
        before entering its raw-ZIP lifecycle, while manifest validation occurs
        inside that lifecycle. Keeping this narrow encoder here preserves that
        ordering without returning an unvalidated package value object.
        """
        if not files or len(files) > MAX_FILES:
            raise SkillPackageInvalidError()
        entries: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        total = 0
        for path, content in files:
            if not isinstance(path, str) or not isinstance(content, bytes):
                raise SkillPackageInvalidError()
            normalized_path = self._normalize_path(path, reject_empty_parts=True)
            if normalized_path in seen:
                raise SkillPackageInvalidError("duplicate_file_path")
            if len(content) > MAX_FILE_BYTES:
                raise SkillPackageTooLargeError()
            seen.add(normalized_path)
            total += len(content)
            if total > MAX_EXPANDED_BYTES:
                raise SkillPackageTooLargeError()
            entries.append((normalized_path, content))
        canonical_zip = self._build_canonical_zip(
            sorted(entries, key=lambda item: item[0].encode("utf-8"))
        )
        if len(canonical_zip) > MAX_COMPRESSED_BYTES:
            raise SkillPackageTooLargeError()
        return canonical_zip

    def _validate_entries(
        self, entries: Sequence[tuple[str, bytes]]
    ) -> ValidatedSkillPackage:
        skill_files = [
            entry for entry in entries if entry[0].split("/")[-1] == "SKILL.md"
        ]
        if not skill_files:
            raise SkillPackageInvalidError("missing_skill_file")
        if len(skill_files) > 1:
            raise SkillPackageInvalidError("multiple_skill_files")

        skill_path, markdown = skill_files[0]
        roots = {path.split("/")[0] for path, _content in entries}
        wrapper = skill_path.split("/")[0] if "/" in skill_path else None
        if wrapper is not None and len(roots) != 1:
            raise SkillPackageInvalidError("invalid_wrapper")

        try:
            try:
                metadata = self._metadata_parser.parse_skill_markdown(
                    markdown, path=skill_path
                ).to_dict()
                SkillParser.parse_config(SkillParser.decode_content(markdown))
            except SkillManifestError as exc:
                if exc.code is not SkillManifestErrorCode.MISSING_FRONTMATTER:
                    raise
                text = SkillParser.decode_content(markdown)
                metadata = SkillParser.parse_legacy_upload_content(text) or {}
        except SkillManifestError as exc:
            raise SkillPackageInvalidError("invalid_metadata") from exc

        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            raise SkillPackageInvalidError("invalid_metadata")
        name, description = name.strip(), description.strip()
        if (
            not name
            or not description
            or not _SKILL_NAME.fullmatch(name)
            or name.lower() in _RESERVED_SKILL_NAMES
        ):
            raise SkillPackageInvalidError("invalid_metadata")
        if wrapper and wrapper != name:
            raise SkillPackageInvalidError("wrapper_name_mismatch")
        if wrapper is not None and any(
            not path.startswith(f"{wrapper}/") for path, _content in entries
        ):
            raise SkillPackageInvalidError("invalid_wrapper")

        normalized = tuple(
            sorted(
                (
                    (path[len(wrapper) + 1 :] if wrapper else path, content)
                    for path, content in entries
                ),
                key=lambda item: item[0].encode("utf-8"),
            )
        )
        canonical_zip = self._build_canonical_zip(normalized)
        if len(canonical_zip) > MAX_COMPRESSED_BYTES:
            raise SkillPackageTooLargeError()
        return ValidatedSkillPackage(
            name=name,
            description=description,
            files=normalized,
            canonical_zip=canonical_zip,
        )

    @staticmethod
    def _normalize_path(path: str, *, reject_empty_parts: bool = False) -> str:
        if (
            not path
            or path.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", path) is not None
            or "\\" in path
            or ".." in path.split("/")
            or len(path) > MAX_PATH_LENGTH
            or (reject_empty_parts and any(part == "" for part in path.split("/")))
        ):
            raise SkillPackageInvalidError("unsafe_file_path")
        normalized = "/".join(part for part in path.split("/") if part not in ("", "."))
        if not normalized:
            raise SkillPackageInvalidError("unsafe_file_path")
        return normalized

    @staticmethod
    def _is_ignored_path(relative_path: str) -> bool:
        parts = relative_path.split("/")
        name = parts[-1]
        return (
            name == ".DS_Store"
            or parts[0] == "__MACOSX"
            or "__pycache__" in parts
            or name.endswith((".pyc", ".pyo"))
        )

    @staticmethod
    def _build_canonical_zip(files: Sequence[tuple[str, bytes]]) -> bytes:
        stream = io.BytesIO()
        try:
            with zipfile.ZipFile(stream, mode="w") as archive:
                for path, content in files:
                    info = zipfile.ZipInfo(path, date_time=_CANONICAL_ZIP_DATE)
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    info.flag_bits |= 0x800
                    archive.writestr(
                        info,
                        content,
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=9,
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            raise SkillPackageInvalidError() from exc
        return stream.getvalue()
