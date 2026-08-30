"""Hermetic git-CLI adapter that returns one frozen Skill repository snapshot."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from agentclaw.community.core.skill_center.git_snapshot import (
    GitSkillSnapshot,
    GitSnapshotError,
    GitSnapshotInvalidError,
    GitSnapshotServiceProtocol,
    select_skill_source_subdir,
)
from agentclaw.community.core.skill_center.skill_package import (
    MAX_EXPANDED_BYTES,
    MAX_FILE_BYTES,
    MAX_FILES,
)


class GitSnapshotService(GitSnapshotServiceProtocol):
    """Clone without credentials and freeze the exact selected subtree."""

    def fetch(
        self, *, git_url: str, branch: str | None, subdir: str | None
    ) -> GitSkillSnapshot:
        url = self._validated_url(git_url)
        branch = self._validated_branch(branch)
        with TemporaryDirectory(prefix="space-skill-git-") as temp:
            checkout = Path(temp) / "repo"
            command = ["git", "clone", "--depth", "1"]
            if branch is not None:
                command.extend(("--branch", branch))
            command.extend(("--", url, str(checkout)))
            self._run(command, cwd=None)
            resolved_branch = self._run(
                ["git", "branch", "--show-current"], cwd=checkout
            ).strip()
            if not resolved_branch:
                resolved_branch = branch or "HEAD"
            commit_sha = self._run(["git", "rev-parse", "HEAD"], cwd=checkout).strip()
            manifests = tuple(
                path.relative_to(checkout).as_posix()
                for path in checkout.rglob("SKILL.md")
                if ".git" not in path.relative_to(checkout).parts
            )
            selected = select_skill_source_subdir(
                manifests, requested_subdir=subdir
            )
            root = checkout / selected if selected else checkout
            files = self._read_tree(root)
            return GitSkillSnapshot(
                repo_url=url,
                resolved_branch=resolved_branch,
                commit_sha=commit_sha,
                source_subdir=selected,
                files=files,
            )

    @staticmethod
    def _validated_url(value: str) -> str:
        normalized = value.strip() if isinstance(value, str) else ""
        parsed = urlsplit(normalized)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.hostname.lower() in {"localhost", "localhost.localdomain"}
        ):
            raise GitSnapshotInvalidError("Git URL must be a credential-free HTTPS URL")
        return normalized

    @staticmethod
    def _validated_branch(value: str | None) -> str | None:
        if value is None:
            return None
        branch = value.strip()
        if (
            not branch
            or len(branch) > 512
            or branch.startswith("-")
            or ".." in branch
            or any(character.isspace() or ord(character) < 32 for character in branch)
        ):
            raise GitSnapshotInvalidError("Git branch is invalid")
        return branch

    @staticmethod
    def _run(command: list[str], *, cwd: Path | None) -> str:
        environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                capture_output=True,
                check=False,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitSnapshotError("Git snapshot command failed") from exc
        if result.returncode != 0:
            raise GitSnapshotError("Git snapshot command failed")
        return result.stdout

    @staticmethod
    def _read_tree(root: Path) -> tuple[tuple[str, bytes], ...]:
        entries: list[tuple[str, bytes]] = []
        total = 0
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().encode()):
            relative = path.relative_to(root)
            if ".git" in relative.parts or path.is_dir():
                continue
            if path.is_symlink() or not path.is_file():
                raise GitSnapshotInvalidError("Git Skill tree contains a special file")
            size = path.stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
                raise GitSnapshotInvalidError("Git Skill tree exceeds package limits")
            entries.append((relative.as_posix(), path.read_bytes()))
            if len(entries) > MAX_FILES:
                raise GitSnapshotInvalidError("Git Skill tree exceeds package limits")
        return tuple(entries)
