"""Strict parser for Skill ``SKILL.md`` manifests.

``SKILL.md`` is the sole source of authoritative Skill metadata.  This module
intentionally does not fall back to README files, directory names, or body
text when parsing ``name`` and ``description``.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from agentclaw.community.log import get_logger
from agentclaw.community.core.skill_center.skill_metadata import (
    SkillManifestError,
    SkillManifestErrorCode,
    SkillManifestValidationIssue,
    SkillManifestValidationResult,
    SkillMetadata,
)


logger = get_logger()

_MAX_SKILL_NAME_LENGTH = 256
_MAX_SKILL_DESCRIPTION_UTF8_BYTES = 65_535


def _validate_manifest_path(path: str) -> None:
    """Accept a relative package path whose canonical leaf is ``SKILL.md``."""
    parsed = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or parsed.is_absolute()
        or ".." in parsed.parts
        or parsed.name != "SKILL.md"
    ):
        raise SkillManifestError(
            SkillManifestErrorCode.INVALID_PATH,
            "Manifest path must be a safe relative path ending in SKILL.md.",
            "path",
        )


@dataclass
class SkillInfo:
    """技能信息数据模型 (文件系统层面)"""

    id: str
    name: str
    description: str = ""
    version: str = "1.0.0"
    category: str = "general"
    icon: str = "🔧"
    path: str = ""
    source_path: str = ""
    is_active: bool = False
    is_installed: bool = False
    capabilities: list[dict] = field(default_factory=list)
    author: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "category": self.category,
            "icon": self.icon,
            "path": self.path,
            "source_path": self.source_path,
            "is_active": self.is_active,
            "is_installed": self.is_installed,
            "capabilities": self.capabilities,
            "author": self.author,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SkillTreeNode:
    """技能市场树节点"""

    name: str
    path: str
    type: str
    children: list["SkillTreeNode"] = field(default_factory=list)
    skill_info: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "name": self.name,
            "path": self.path,
            "type": self.type,
            "children": [c.to_dict() for c in self.children],
        }
        if self.skill_info:
            result["skill_info"] = self.skill_info
        return result


def _extract_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Return strict YAML frontmatter and Markdown body."""
    normalized = content.lstrip("\ufeff")
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillManifestError(
            "MISSING_FRONTMATTER",
            "SKILL.md must start with YAML frontmatter.",
        )

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing_index is None:
        raise SkillManifestError(
            "INVALID_FRONTMATTER",
            "SKILL.md frontmatter is not closed.",
        )

    frontmatter_text = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :])
    try:
        data = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise SkillManifestError(
            "INVALID_FRONTMATTER",
            f"SKILL.md frontmatter is invalid: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise SkillManifestError(
            "INVALID_FRONTMATTER",
            "SKILL.md frontmatter must be a YAML mapping.",
        )
    return data, body


def _validate_manifest(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    for field_name in ("name", "description"):
        if field_name not in data:
            raise SkillManifestError(
                f"MISSING_{field_name.upper()}",
                f"SKILL.md must contain required field: {field_name}.",
                field_name,
            )
        value = data[field_name]
        if not isinstance(value, str):
            raise SkillManifestError(
                f"INVALID_{field_name.upper()}_TYPE",
                f"SKILL.md field '{field_name}' must be a string.",
                field_name,
            )
        value = value.strip()
        if not value:
            raise SkillManifestError(
                f"EMPTY_{field_name.upper()}",
                f"SKILL.md field '{field_name}' cannot be empty.",
                field_name,
            )
        result[field_name] = value

    if len(result["name"]) > _MAX_SKILL_NAME_LENGTH:
        raise SkillManifestError(
            SkillManifestErrorCode.NAME_TOO_LONG,
            f"SKILL.md field 'name' cannot exceed {_MAX_SKILL_NAME_LENGTH} characters.",
            "name",
        )
    if len(result["description"].encode("utf-8")) > _MAX_SKILL_DESCRIPTION_UTF8_BYTES:
        raise SkillManifestError(
            SkillManifestErrorCode.DESCRIPTION_TOO_LONG,
            "SKILL.md field 'description' cannot exceed "
            f"{_MAX_SKILL_DESCRIPTION_UTF8_BYTES} UTF-8 bytes.",
            "description",
        )

    return result


def _to_skill_info(data: dict[str, Any]) -> dict[str, Any]:
    """Project validated frontmatter into the existing SkillInfo dictionary."""
    skill_info: dict[str, Any] = {
        "name": data["name"],
        "description": data["description"],
        "version": "1.0.0",
        "category": "general",
        "author": "",
        "tags": [],
        "input_schema": "",
        "output_schema": "",
        "capabilities": [],
    }
    if "version" in data:
        skill_info["version"] = data["version"]
    if "author" in data:
        skill_info["author"] = data["author"]
    if "tags" in data:
        tags = data["tags"]
        if isinstance(tags, list):
            skill_info["tags"] = tags
        elif isinstance(tags, str):
            skill_info["tags"] = [
                item.strip() for item in tags.split(",") if item.strip()
            ]
    return skill_info


class SkillParser:
    """Strict parser for ``SKILL.md`` metadata with UTF-8/GBK compatibility."""

    @staticmethod
    def parse_skill_markdown(
        content: str | bytes, *, path: str = "SKILL.md"
    ) -> SkillMetadata:
        """Return authoritative metadata through the reusable parser seam."""
        _validate_manifest_path(path)
        if isinstance(content, bytes):
            content = SkillParser.decode_content(content)
        frontmatter, _body = _extract_frontmatter(content)
        validated = _validate_manifest(frontmatter)
        return SkillMetadata(
            name=validated["name"], description=validated["description"]
        )

    @staticmethod
    def validate_skill_markdown(
        content: str | bytes, *, path: str = "SKILL.md"
    ) -> SkillManifestValidationResult:
        """Validate without making callers depend on exception messages."""
        try:
            metadata = SkillParser.parse_skill_markdown(content, path=path)
        except SkillManifestError as exc:
            return SkillManifestValidationResult(
                metadata=None,
                errors=(SkillManifestValidationIssue(code=exc.code, field=exc.field),),
            )
        return SkillManifestValidationResult(metadata=metadata)

    @staticmethod
    def decode_content(content: bytes) -> str:
        """Decode SKILL.md without silently replacing invalid bytes."""
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return content.decode("gbk")
            except UnicodeDecodeError as exc:
                raise SkillManifestError(
                    "INVALID_ENCODING",
                    "SKILL.md must be encoded as UTF-8 or GBK.",
                ) from exc

    @staticmethod
    def decode_content_for_display(content: bytes) -> str:
        """Decode legacy display content without making reads unavailable.

        Upload and manifest validation use :meth:`decode_content` and remain
        strict. Existing installed content may predate that contract, so the
        read-only display path preserves the historical replacement fallback.
        """
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return content.decode("gbk")
            except UnicodeDecodeError:
                return content.decode("utf-8", errors="replace")

    @staticmethod
    def find_skill_file(skill_path: Path) -> Path | None:
        """Return only the target directory's authoritative ``SKILL.md``."""
        skill_file = skill_path / "SKILL.md"
        return skill_file if skill_file.is_file() else None

    @staticmethod
    def find_display_file(skill_path: Path) -> Path | None:
        """Return the historical display document, preferring ``SKILL.md``."""
        for filename in ("SKILL.md", "README.md"):
            candidate = skill_path / filename
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def has_skill_file(skill_path: Path) -> bool:
        return (skill_path / "SKILL.md").is_file()

    @classmethod
    def parse(cls, skill_path: Path) -> dict[str, Any] | None:
        """Parse a package whose wrapper directory is part of its identity."""
        return cls._parse_path(skill_path, require_directory_name_match=True)

    @classmethod
    def parse_repository(cls, skill_path: Path) -> dict[str, Any] | None:
        """Parse a governed Repo asset with path and display-name separation.

        Repo catalog identity is its ``git://`` directory path while the
        manifest ``name`` is display metadata.  Historical catalog entries
        intentionally allow those values to differ.
        """
        return cls._parse_path(skill_path, require_directory_name_match=False)

    @classmethod
    def _parse_path(
        cls,
        skill_path: Path,
        *,
        require_directory_name_match: bool,
    ) -> dict[str, Any] | None:
        skill_file = cls.find_skill_file(skill_path)
        if not skill_file:
            return None
        try:
            content = cls.decode_content(skill_file.read_bytes())
        except OSError as exc:
            logger.error("[SkillParser] Error reading %s: %s", skill_file, exc)
            raise SkillManifestError("SKILL_FILE_READ_ERROR", str(exc)) from exc

        skill_info = cls.parse_content(content)
        if require_directory_name_match and skill_info["name"] != skill_path.name:
            raise SkillManifestError(
                "NAME_DIRECTORY_MISMATCH",
                "Skill folder name must match SKILL.md field 'name'. "
                f"Folder name: '{skill_path.name}', SKILL.md name: '{skill_info['name']}'.",
                "name",
            )
        stat = skill_file.stat()
        skill_info["created_at"] = datetime.fromtimestamp(stat.st_ctime).isoformat()
        skill_info["updated_at"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
        body = _extract_frontmatter(content)[1]
        for capability in re.findall(r"^##\s+(.+)$", body, re.MULTILINE):
            skill_info["capabilities"].append(
                {
                    "id": capability.lower().replace(" ", "_"),
                    "name": capability,
                }
            )
        return skill_info

    @staticmethod
    def parse_content(content: str) -> dict[str, Any] | None:
        """Parse strict UTF-8-decoded ``SKILL.md`` content."""
        if not content:
            return None
        frontmatter, _body = _extract_frontmatter(content)
        metadata = SkillParser.parse_skill_markdown(content)
        projection = dict(frontmatter)
        projection.update(metadata.to_dict())
        return _to_skill_info(projection)

    @staticmethod
    def parse_config(content: str) -> list[dict[str, Any]]:
        """Return the optional, raw ``config`` declaration from SKILL.md.

        Config is an execution-time parameter contract, not catalog metadata;
        it intentionally remains outside the #1221 catalog projection.
        """
        frontmatter, _body = _extract_frontmatter(content)
        config = frontmatter.get("config", [])
        if not isinstance(config, list) or not all(
            isinstance(item, dict) for item in config
        ):
            raise SkillManifestError(
                "INVALID_CONFIG", "SKILL.md config must be a list.", "config"
            )
        return config

    @staticmethod
    def parse_installed_content(content: str) -> dict[str, Any] | None:
        """Project metadata from a legacy installed Skill for read-only lists.

        Historical active Skills can have only ``name`` in frontmatter. They
        remain visible, with an empty description, while new uploads continue
        to use the strict :meth:`parse_content` contract.
        """
        if not content:
            return None
        frontmatter, _body = _extract_frontmatter(content)
        name = frontmatter.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SkillManifestError(
                "MISSING_NAME", "SKILL.md must contain a non-empty name.", "name"
            )
        normalized = dict(frontmatter)
        normalized["name"] = name.strip()
        description = normalized.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise SkillManifestError(
                "INVALID_DESCRIPTION_TYPE",
                "SKILL.md field 'description' must be a string.",
                "description",
            )
        normalized["description"] = description.strip()
        return _to_skill_info(normalized)

    @staticmethod
    def parse_legacy_upload_content(content: str) -> dict[str, Any] | None:
        """Parse the pre-frontmatter upload shape for endpoint compatibility."""
        if not content:
            return None
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise SkillManifestError(
                "INVALID_FRONTMATTER", f"Legacy SKILL.md metadata is invalid: {exc}"
            ) from exc
        if not isinstance(data, dict):
            return None
        return _to_skill_info(_validate_manifest(data))
