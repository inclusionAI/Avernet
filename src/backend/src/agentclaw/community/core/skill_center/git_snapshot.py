"""Contracts and deterministic selection rules for Space Skill Git snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable


class GitSnapshotError(RuntimeError):
    """A repository could not be resolved into one immutable snapshot."""


class GitSnapshotInvalidError(GitSnapshotError):
    """The requested repository path or selected Skill tree is invalid."""


@dataclass(frozen=True, slots=True)
class GitSkillSnapshot:
    repo_url: str
    resolved_branch: str
    commit_sha: str
    source_subdir: str
    files: tuple[tuple[str, bytes], ...]


def _normalized_relative_path(value: str, *, allow_root: bool) -> str:
    if not isinstance(value, str) or "\\" in value or value.startswith("/"):
        raise GitSnapshotInvalidError("Git snapshot path must be POSIX relative")
    path = PurePosixPath(value)
    if any(part in {"..", ""} for part in path.parts):
        raise GitSnapshotInvalidError("Git snapshot path escapes the repository")
    normalized = path.as_posix()
    if normalized == ".":
        if allow_root:
            return ""
        raise GitSnapshotInvalidError("Git snapshot file path is empty")
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
            raise GitSnapshotInvalidError("requested Git subdir has no SKILL.md")
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
        raise GitSnapshotInvalidError("Git repository contains no SKILL.md")
    return min(parents, key=lambda value: value.encode("utf-8"))


@runtime_checkable
class GitSnapshotServiceProtocol(Protocol):
    def fetch(
        self, *, git_url: str, branch: str | None, subdir: str | None
    ) -> GitSkillSnapshot: ...
