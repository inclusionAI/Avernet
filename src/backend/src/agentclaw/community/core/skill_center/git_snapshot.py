"""Contracts and deterministic selection rules for Space Skill Git snapshots."""

from __future__ import annotations

from pathlib import PurePosixPath

from agentclaw.community.core.skill_center.skill_package import (
    SkillManifestMissingError,
    SkillPathInvalidError,
)


def _normalized_relative_path(value: str, *, allow_root: bool) -> str:
    if not isinstance(value, str) or "\\" in value or value.startswith("/"):
        raise SkillPathInvalidError("unsafe_file_path")
    path = PurePosixPath(value)
    if any(part in {"..", ""} for part in path.parts):
        raise SkillPathInvalidError("unsafe_file_path")
    normalized = path.as_posix()
    if normalized == ".":
        if allow_root:
            return ""
        raise SkillPathInvalidError("unsafe_file_path")
    return normalized.rstrip("/")


def select_skill_source_subdir(
    manifest_paths: tuple[str, ...], *, requested_subdir: str | None
) -> str:
    """Choose root first, otherwise normalized parent bytewise ascending."""

    normalized_manifests = {
        _normalized_relative_path(path, allow_root=False) for path in manifest_paths
    }
    if requested_subdir is not None:
        subdir = _normalized_relative_path(requested_subdir, allow_root=True)
        expected = f"{subdir}/SKILL.md" if subdir else "SKILL.md"
        if expected not in normalized_manifests:
            raise SkillManifestMissingError("missing_skill_file")
        return subdir
    if "SKILL.md" in normalized_manifests:
        return ""
    parents = {
        PurePosixPath(path).parent.as_posix()
        for path in normalized_manifests
        if PurePosixPath(path).name == "SKILL.md"
    }
    parents.discard(".")
    if not parents:
        raise SkillManifestMissingError("missing_skill_file")
    return min(parents, key=lambda value: value.encode("utf-8"))
