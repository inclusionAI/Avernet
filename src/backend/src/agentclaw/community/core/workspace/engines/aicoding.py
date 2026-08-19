from __future__ import annotations

from pathlib import Path, PurePosixPath

from agentclaw.community.core.workspace.engine_sandbox import DirectoryItem, EngineBuildPlan, ReadOnlyRule
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger

logger = get_logger()


_AICODING_RSYNC_EXCLUDES = [
    "workspace/.claude",
    "projects",
    "sessions",
    "shell-snapshots",
    ".last-cleanup",
    "backups",
    "telemetry",
    "session-env",
    # 锚定到 source 根: 主 rsync(source=.aicoding/)需要排掉根下的 OSS 挂载点
    # .aicoding/skills-repo; 但 extra_sync(source=.claude/)的同一份 excludes
    # 不该误伤 .claude/skills/skills-repo 这条转接软链(它在草稿态有,build 后必须
    # 跟着进 NFS 副本,否则装载到预发容器后 .claude/skills/ 下所有具体 skill 软链
    # 全 dangling)。"skills-repo" 无锚定会任意层级匹配,加 leading / 锚定 source
    # 根: 主 rsync 仍排 .aicoding/skills-repo (✅ 行为不变);
    # extra_sync 的 .claude/ 根下没有 skills-repo,exclude 打不到,转接软链能正确同步。
    "/skills-repo",
    "enterprise_device_key.pem",
    "last_engine",
    "agents/*/sessions/",
    "workspace/memory/",
    "workspace/config/mcporter.json",
    "workspace/.learnings/",
    "workspace/*/.git/",
    "workspace/skills/skills-repo",
    "workspace/skills/skills-center",
    "workspace/skills-pool/skills-repo",
    "workspace/skills-pool/.skills-repo*",
    "skills/*/.git/",
    "memory/",
    "logs/",
    "subagents/",
    "canvas/",
    "update-check.json",
    "session_user_map.json",
    "cron/runs/",
    "identity/device.json",
    "/agents",
    ".claude/agents",
]


_AICODING_DEFAULT_RULES = [
    ReadOnlyRule(path="workspace/config/mcporter.json", rule_type="file"),
    ReadOnlyRule(path="workspace/*.md", rule_type="glob"),
    ReadOnlyRule(path="/home/admin/.mcporter/mcporter.json", rule_type="file"),
    ReadOnlyRule(path="workspace/.claude/settings.json", rule_type="file"),
    ReadOnlyRule(path="workspace/.claude/models.json", rule_type="file"),
    ReadOnlyRule(path="workspace/.claude/config.json", rule_type="file"),
    ReadOnlyRule(path="settings.json", rule_type="file"),
    ReadOnlyRule(path="models.json", rule_type="file"),
    ReadOnlyRule(path="config.json", rule_type="file"),
    ReadOnlyRule(path="workspace/skills-local", rule_type="glob"),
]


def _make_aicoding_build_plan(rsync_excludes: list[str]) -> EngineBuildPlan:
    """Factory function to create build plan with given excludes."""
    return EngineBuildPlan(
        engine_type="aicoding",
        source_root_name=".aicoding",
        migration_subpath="aicoding",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        extra_sync_source_relpath=".claude",
        extra_sync_target_relpath="claude",
        rsync_excludes=rsync_excludes,
        extra_include_files=["sessions/cron-tasks.json"],
    )


_AICODING_BUILD_PLAN = _make_aicoding_build_plan(list(_AICODING_RSYNC_EXCLUDES))


class AICodingSandboxProvider:
    """Workspace provider for the AICoding engine.

    The base path comes from the injected :class:`WorkspaceConfig`;
    application-prod.yaml leaves it at the sandbox mount, dev/local
    overrides it to the dev's home (`~/.aicoding`). The provider
    does not branch on runtime mode.
    """

    def __init__(self, workspace: cfg.WorkspaceConfig) -> None:
        self._workspace = workspace

    @property
    def engine_type(self) -> str:
        return "aicoding"

    def get_base_path(self) -> str:
        return self._workspace.aicoding_root

    def get_sessions_dir(self) -> str:
        return f"{self._workspace.aicoding_root}/projects"

    def get_default_read_only_rules(self) -> list[ReadOnlyRule]:
        return list(_AICODING_DEFAULT_RULES)

    def get_build_plan(
        self,
        build_rsync_excludes_append: list[str] | None = None,
    ) -> EngineBuildPlan:
        # 合并模式：默认值 + 自定义项（去重）
        excludes = list(_AICODING_RSYNC_EXCLUDES)
        if build_rsync_excludes_append:
            # 合并并去重，保持顺序：默认值在前，自定义项追加
            for item in build_rsync_excludes_append:
                if item not in excludes:
                    excludes.append(item)
        return _make_aicoding_build_plan(excludes)

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

    async def list_directory(
        self,
        sub_path: str = "",
        recursive: bool = False,
        *,
        device_fs=None,
    ) -> list[DirectoryItem]:
        sub_path = self._normalize_sub_path(sub_path)
        items: list[DirectoryItem] = []
        base_path = self._workspace.aicoding_root

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

        # No sandbox binding — walk the local filesystem at aicoding_root.
        root = Path(base_path)
        local_target = root / sub_path if sub_path else root
        if local_target.exists() and local_target.is_dir():
            entries = local_target.rglob("*") if recursive else local_target.iterdir()
            for entry in entries:
                rel = str(entry.relative_to(root))
                items.append(DirectoryItem(name=entry.name, path=rel, is_dir=entry.is_dir()))
        else:
            logger.info(
                "[AICodingSandboxProvider.list_directory] No sandbox binding and "
                "local root %s does not exist; returning empty tree",
                root,
            )
        return items
