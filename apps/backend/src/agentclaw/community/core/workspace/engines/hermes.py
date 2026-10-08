"""Hermes sandbox build adapter.

Center corpus paths are deliberately absent here: they arrive as frozen,
versioned Engine Runtime evidence and are translated by ``BotBuildService``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from agentclaw.community.core.workspace.engine_sandbox import (
    DirectoryItem,
    EngineBuildPlan,
    ReadOnlyRule,
)
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger

logger = get_logger()


_HERMES_RSYNC_EXCLUDES = [
    "/skills-repo",
    "workspace/memory/",
    "workspace/config/mcporter.json",
    "workspace/.git/",
    "workspace/*/.git/",
    "workspace/skills/skills-repo",
    "workspace/skills-pool/skills-repo",
    "workspace/skills-pool/.skills-repo*",
    "skills/*/.git/",
    "logs/",
    "sessions/",
]

_HERMES_DEFAULT_RULES = [
    ReadOnlyRule(path="config.yaml", rule_type="file"),
    ReadOnlyRule(path="workspace/config/mcporter.json", rule_type="file"),
    ReadOnlyRule(path="workspace/*.md", rule_type="glob"),
    ReadOnlyRule(path="workspace/skills/skills-local", rule_type="glob"),
]


def _make_hermes_build_plan(rsync_excludes: list[str]) -> EngineBuildPlan:
    return EngineBuildPlan(
        engine_type="hermes",
        source_root_name=".hermes",
        migration_subpath="hermes",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="skills",
        skill_target_relpath="skills",
        rsync_excludes=rsync_excludes,
    )


class HermesSandboxProvider:
    """Workspace/build provider for the Hermes filesystem engine."""

    def __init__(self, workspace: cfg.WorkspaceConfig) -> None:
        self._workspace = workspace

    @property
    def engine_type(self) -> str:
        return "hermes"

    def get_base_path(self) -> str:
        return self._workspace.hermes_root

    def get_sessions_dir(self) -> str:
        return f"{self.get_base_path()}/sessions"

    def get_default_read_only_rules(self) -> list[ReadOnlyRule]:
        return list(_HERMES_DEFAULT_RULES)

    def get_build_plan(
        self,
        build_rsync_excludes_append: list[str] | None = None,
        bot: dict[str, Any] | None = None,
    ) -> EngineBuildPlan:
        excludes = list(_HERMES_RSYNC_EXCLUDES)
        for item in build_rsync_excludes_append or ():
            if item not in excludes:
                excludes.append(item)
        return _make_hermes_build_plan(excludes)

    @staticmethod
    def _normalize_sub_path(sub_path: str) -> str:
        if not sub_path:
            return ""
        if "\x00" in sub_path:
            raise ValueError(f"Invalid sub_path: {sub_path!r}")
        path = PurePosixPath(sub_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"Invalid sub_path: {sub_path}")
        normalized = str(path)
        return "" if normalized == "." else normalized

    async def list_directory(
        self,
        sub_path: str = "",
        recursive: bool = False,
        *,
        device_fs=None,
    ) -> list[DirectoryItem]:
        sub_path = self._normalize_sub_path(sub_path)
        items: list[DirectoryItem] = []
        base_path = self.get_base_path()
        if device_fs is not None:
            async def walk(current_sub_path: str) -> None:
                target = (
                    f"{base_path}/{current_sub_path}"
                    if current_sub_path
                    else base_path
                )
                for item in await device_fs.list_dir(target, recursive=False) or ():
                    name = item.get("name", "")
                    if not name:
                        continue
                    rel = f"{current_sub_path}/{name}" if current_sub_path else name
                    is_dir = item.get("is_dir", False)
                    items.append(DirectoryItem(name=name, path=rel, is_dir=is_dir))
                    if recursive and is_dir:
                        await walk(rel)

            await walk(sub_path)
            return items

        root = Path(base_path)
        target = root / sub_path if sub_path else root
        if target.exists() and target.is_dir():
            entries = target.rglob("*") if recursive else target.iterdir()
            for entry in entries:
                items.append(
                    DirectoryItem(
                        name=entry.name,
                        path=str(entry.relative_to(root)),
                        is_dir=entry.is_dir(),
                    )
                )
        else:
            logger.info(
                "[HermesSandboxProvider.list_directory] local root %s is absent",
                root,
            )
        return items


__all__ = ["HermesSandboxProvider"]
