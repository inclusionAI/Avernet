"""Host-filesystem skill symlink synchronization for the local test runtime."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from agentclaw.community.log import get_logger

logger = get_logger()

_RESERVED_NAMES = frozenset({
    "skills-repo",
    "skills-local",
    ".current_skill_set",
    "skill_sets.json",
})
_CONTAINER_PREFIXES = (
    "/home/admin/.openclaw/workspace/skills/",
    "/home/admin/.claude_code/skills/",
    "/home/admin/.aicoding/skills/",
)


class LocalSkillSymlinkSynchronizer:
    """Synchronize the local test runtime's OpenClaw skill links."""

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills_dir = skills_dir or Path.home() / ".openclaw" / "workspace" / "skills"
        self._repo_source_candidates = (Path.home() / "aiworkbench" / "skills-repo",)

    def sync(self, symlinks: list[dict[str, str]]) -> dict[str, Any]:
        """Make the managed symlinks under ``skills_dir`` match ``symlinks``."""
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_skills_repo()

        desired_names: set[str] = set()
        created = 0
        skipped = 0
        for mapping in symlinks:
            source = mapping.get("source", "")
            target = mapping.get("target", "")
            if not source or not target:
                logger.warning("[LocalSkillSymlinkSynchronizer] skipping invalid mapping: %s", mapping)
                skipped += 1
                continue

            source_path = self._map_container_path(source)
            try:
                link_path = self._managed_target(target)
            except ValueError as exc:
                logger.warning("[LocalSkillSymlinkSynchronizer] skipping unsafe target: %s", exc)
                skipped += 1
                continue

            desired_names.add(link_path.name)
            if not source_path.exists():
                logger.warning(
                    "[LocalSkillSymlinkSynchronizer] source does not exist: %s",
                    source_path,
                )
                skipped += 1
                continue

            if link_path.is_symlink() or link_path.exists():
                if link_path.is_symlink() or link_path.is_file():
                    link_path.unlink()
                else:
                    shutil.rmtree(link_path)

            try:
                try:
                    link_path.symlink_to(source_path.relative_to(self._skills_dir))
                except ValueError:
                    link_path.symlink_to(source_path)
                created += 1
            except OSError as exc:
                logger.error(
                    "[LocalSkillSymlinkSynchronizer] failed to create %s -> %s: %s",
                    link_path,
                    source_path,
                    exc,
                )
                skipped += 1

        removed = 0
        for item in self._skills_dir.iterdir():
            if item.name in _RESERVED_NAMES or item.name in desired_names:
                continue
            if item.is_symlink():
                item.unlink()
                removed += 1

        return {
            "success": True,
            "message": (
                f"local sync done: created={created}, removed={removed}, "
                f"skipped={skipped}"
            ),
            "created": created,
            "removed": removed,
            "skipped": skipped,
        }

    def _ensure_skills_repo(self) -> None:
        target = self._skills_dir / "skills-repo"
        source = self._find_repo_source()
        if source is None:
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
            return

        if target.is_symlink() and target.resolve() == source.resolve():
            return
        if target.is_symlink() or target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(source)

    def _find_repo_source(self) -> Path | None:
        for candidate in self._repo_source_candidates:
            if candidate.is_dir():
                return candidate
        try:
            from agentclaw.community.core.workspace.path_factory import (
                get_global_skills_repo_dir,
            )

            shared = get_global_skills_repo_dir()
            if shared.is_dir():
                return shared
        except Exception:
            logger.debug("Unable to resolve the shared skills repository", exc_info=True)
        return None

    def _map_container_path(self, path: str) -> Path:
        for prefix in _CONTAINER_PREFIXES:
            if path.startswith(prefix):
                return self._skills_dir / path[len(prefix):]
        return Path(path)

    def _managed_target(self, path: str) -> Path:
        candidate = self._map_container_path(path)
        base = Path(os.path.abspath(self._skills_dir))
        normalized = Path(os.path.abspath(candidate))
        try:
            normalized.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"target escapes skills directory: {path}") from exc
        return normalized
