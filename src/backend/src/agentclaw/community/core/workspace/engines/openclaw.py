from __future__ import annotations

import fnmatch
from pathlib import Path, PurePosixPath

from agentclaw.community.core.workspace.engine_sandbox import DirectoryItem, EngineBuildPlan, ReadOnlyRule
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger

logger = get_logger()


_OPENCLAW_RSYNC_EXCLUDES = [
    "enterprise_device_key.pem",
    "last_engine",
    "agents/*/sessions/",
    "workspace/memory/",
    "workspace/config/mcporter.json",
    "workspace/.learnings/",
    "workspace/.openclaw/",
    "workspace/.git/",
    "workspace/*/.git/",
    "workspace/skills/skills-repo",
    "workspace/skills/.skills-repo*",
    "workspace/skills/skills-center",
    "workspace/skills-pool/skills-repo",
    "workspace/skills-pool/.skills-repo*",
    "workspace/clawbench_results/",
    "workspace/clawbench_template_generate/",
    "skills/*/.git/",
    "memory/",
    "logs/",
    "subagents/",
    "canvas/",
    "update-check.json",
    "session_user_map.json",
    "cron/runs/",
    "identity/device.json",
]


_OPENCLAW_DEFAULT_RULES = [
    ReadOnlyRule(path="openclaw.json", rule_type="file"),
    ReadOnlyRule(path="workspace/config/mcporter.json", rule_type="file"),
    ReadOnlyRule(path="workspace/*.md", rule_type="glob"),
    ReadOnlyRule(path="/home/admin/.mcporter/mcporter.json", rule_type="file"),
    ReadOnlyRule(path="agents/*/agent/models.json", rule_type="glob"),
    ReadOnlyRule(path="workspace/skills/skills-local", rule_type="glob"),
]


def _make_openclaw_build_plan(rsync_excludes: list[str]) -> EngineBuildPlan:
    """Factory function to create build plan with given excludes."""
    return EngineBuildPlan(
        engine_type="openclaw",
        source_root_name=".openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=rsync_excludes,
    )


_OPENCLAW_BUILD_PLAN = _make_openclaw_build_plan(list(_OPENCLAW_RSYNC_EXCLUDES))


class OpenClawSandboxProvider:
    """Workspace provider for the OpenClaw engine.

    The base path comes from the injected :class:`WorkspaceConfig` —
    application-prod.yaml leaves it at the sandbox mount, dev/local
    overrides it to the dev's home (`~/.openclaw`). The provider does
    not branch on runtime mode.
    """

    def __init__(self, workspace: cfg.WorkspaceConfig) -> None:
        self._workspace = workspace

    @property
    def engine_type(self) -> str:
        return "openclaw"

    def get_base_path(self) -> str:
        return self._workspace.openclaw_root

    def get_sessions_dir(self) -> str:
        return f"{self.get_base_path()}/agents"

    def get_default_read_only_rules(self) -> list[ReadOnlyRule]:
        return list(_OPENCLAW_DEFAULT_RULES)

    def get_build_plan(
        self,
        build_rsync_excludes_append: list[str] | None = None,
    ) -> EngineBuildPlan:
        # 合并模式：默认值 + 自定义项（去重）
        excludes = list(_OPENCLAW_RSYNC_EXCLUDES)
        if build_rsync_excludes_append:
            # 合并并去重，保持顺序：默认值在前，自定义项追加
            for item in build_rsync_excludes_append:
                if item not in excludes:
                    excludes.append(item)
        return _make_openclaw_build_plan(excludes)

    def _normalize_sub_path(self, sub_path: str) -> str:
        if not sub_path:
            return ""
        if "\x00" in sub_path:
            raise ValueError(f"Invalid sub_path: {sub_path!r}")
        p = PurePosixPath(sub_path)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"Invalid sub_path: {sub_path}")
        normalized = str(p)
        return "" if normalized == "." else normalized

    def _is_rsync_excluded(self, rel_path: str) -> bool:
        """Return whether a path should be hidden from the tree response.

        The build flow excludes these paths when rsyncing an OpenClaw
        workspace. The read-only tree should not expose them either. Treat
        directory exclude rules as excluding the directory and all descendants.
        """
        rel = rel_path.strip("/")
        if not rel:
            return False

        ancestors = []
        current = PurePosixPath(rel)
        while True:
            ancestors.append(str(current))
            if current.parent == current or str(current.parent) == ".":
                break
            current = current.parent

        for raw_pattern in _OPENCLAW_RSYNC_EXCLUDES:
            pattern = raw_pattern.strip("/")
            if not pattern:
                continue

            # Directory rules in rsync excludes end with '/'. They should hide
            # both the directory itself and every child under it. Wildcards are
            # supported for rules such as ``workspace/*/.git/``.
            if raw_pattern.endswith("/"):
                for ancestor in ancestors:
                    if fnmatch.fnmatchcase(ancestor, pattern):
                        return True
                continue

            if fnmatch.fnmatchcase(rel, pattern):
                return True

            # Some exclude entries do not end with '/', but may still match a
            # directory prefix, e.g. ``workspace/skills/.skills-repo*``. If any
            # ancestor matches, hide descendants as well.
            for ancestor in ancestors[1:]:
                if fnmatch.fnmatchcase(ancestor, pattern):
                    return True

        return False

    async def list_directory(
        self,
        sub_path: str = "",
        recursive: bool = False,
        *,
        device_fs=None,
    ) -> list[DirectoryItem]:
        sub_path = self._normalize_sub_path(sub_path)
        items: list[DirectoryItem] = []
        base_path = self._workspace.openclaw_root

        if device_fs is not None:
            async def walk(current_sub_path: str) -> None:
                target_path = (
                    f"{base_path}/{current_sub_path}"
                    if current_sub_path
                    else base_path
                )
                # Do not rely on the device's recursive flag here. Some sandbox
                # versions return an empty file list when recursive=true, so
                # expand recursion client-side with stable one-level list calls.
                items_data = await device_fs.list_dir(target_path, recursive=False)
                if not items_data:
                    return

                for item in items_data:
                    name = item.get("name", "")
                    if not name:
                        continue
                    rel = f"{current_sub_path}/{name}" if current_sub_path else name
                    if self._is_rsync_excluded(rel):
                        continue
                    is_dir = item.get("is_dir", False)
                    items.append(DirectoryItem(
                        name=name,
                        path=rel,
                        is_dir=is_dir,
                    ))
                    if recursive and is_dir:
                        await walk(rel)

            await walk(sub_path)
            return items

        # No sandbox binding — walk the local filesystem at openclaw_root.
        root = Path(base_path)
        local_target = root / sub_path if sub_path else root
        if local_target.exists() and local_target.is_dir():
            entries = local_target.rglob("*") if recursive else local_target.iterdir()
            for entry in entries:
                rel = str(entry.relative_to(root))
                items.append(DirectoryItem(name=entry.name, path=rel, is_dir=entry.is_dir()))
        else:
            logger.info(
                "[OpenClawSandboxProvider.list_directory] No sandbox binding and "
                "local root %s does not exist; returning empty tree",
                root,
            )
        return items
