"""Plugin API for immutable external Space Skill source acquisition.

Core owns selection, validation, idempotency and persistence policy.  This
boundary owns the two external I/O operations used by that policy: resolving a
credential-free Git snapshot and downloading an exact Skill Center package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentclaw.community.plugin_api.base import Plugin


class GitSnapshotError(RuntimeError):
    """A repository could not be resolved into one immutable snapshot."""


class GitSnapshotInvalidError(GitSnapshotError):
    """The requested repository path or selected Skill tree is invalid."""


class ExactSkillPackageFetchError(RuntimeError):
    """An exact package could not be downloaded or failed its digest check."""


@dataclass(frozen=True, slots=True)
class GitSkillSnapshot:
    repo_url: str
    resolved_branch: str
    commit_sha: str
    source_subdir: str
    files: tuple[tuple[str, bytes], ...]


@runtime_checkable
class SpaceSkillSourcePlugin(Plugin, Protocol):
    """Acquire immutable external source bytes without exposing transports."""

    def fetch_git_snapshot(
        self, *, git_url: str, branch: str | None, subdir: str | None
    ) -> GitSkillSnapshot: ...

    def fetch_exact_package(self, *, url: str, expected_sha256: str) -> bytes: ...

