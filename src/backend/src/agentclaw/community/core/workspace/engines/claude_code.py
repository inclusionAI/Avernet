from __future__ import annotations

from pathlib import Path, PurePosixPath

from agentclaw.community.core.workspace.engine_sandbox import DirectoryItem, EngineBuildPlan, ReadOnlyRule
from agentclaw.community.di import config as cfg
from agentclaw.community.log import get_logger

logger = get_logger()


# ============================================================================
# TODO(claude-code-sandbox-recheck): 以下字段当前沿用自 OpenClawSandboxProvider,
# 尚未按 claude_code 引擎的真实 VM 目录约定逐项核对。待拿到 start_service.sh /
# finalize.sh 已就位的 claude_code 引擎目录权威清单后,单独开 change 复评估并调整。
#
# 待复评估字段（不在本 change 内修改值,仅记录）:
#   - _CLAUDE_CODE_RSYNC_EXCLUDES: 与 openclaw 几乎相同,仅去掉了
#       "workspace/.openclaw/" 和 "workspace/skills/.skills-repo*"。
#       claude_code 是否有引擎专属临时目录需要新增排除?
#   - _DEFAULT_RULES: 路径名已替换,但缺少 openclaw 的
#       "agents/*/agent/models.json" 规则。claude_code 是否需要等价规则?
#   - _BUILD_PLAN.workspace_subdir = "workspace": 是否符合 claude_code 实际布局?
#   - _BUILD_PLAN.mcp_config_relpath = "workspace/config/mcporter.json":
#       finalize.sh 双目录软链接的落点是否就是这里?
#   - _BUILD_PLAN.skill_source_relpath / skill_target_relpath = "workspace/skills":
#       是否与 claude_code 引擎 skills 目录布局一致?
#
# _SANDBOX_ROOT / _LOCAL_ROOT 已与 start_service.sh / finalize.sh 对齐,不需复评估。
# ============================================================================


_CLAUDE_CODE_RSYNC_EXCLUDES = [
    "workspace/.claude",
    "projects",
    "sessions",
    "shell-snapshots",
    ".last-cleanup",
    "backups",
    "telemetry",
    "session-env",
    # 锚定到 source 根: 主 rsync(source=.claude_code/)需要排掉根下的 OSS 挂载点
    # .claude_code/skills-repo; 但 extra_sync(source=.claude/)的同一份 excludes
    # 不该误伤 .claude/skills/skills-repo 这条转接软链(它在草稿态有,build 后必须
    # 跟着进 NFS 副本,否则装载到预发容器后 .claude/skills/ 下所有具体 skill 软链
    # 全 dangling)。"skills-repo" 无锚定会任意层级匹配,加 leading / 锚定 source
    # 根: 主 rsync 仍排 .claude_code/skills-repo (✅ 行为不变);
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


_CLAUDE_CODE_DEFAULT_RULES = [
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


_CLAUDE_CODE_BUILD_PLAN = EngineBuildPlan(
    engine_type="claude_code",
    source_root_name=".claude_code",
    migration_subpath="claude_code",
    workspace_subdir="workspace",
    mcp_config_relpath="workspace/config/mcporter.json",
    skill_source_relpath="workspace/skills",
    skill_target_relpath="workspace/skills",
    extra_sync_source_relpath=".claude",
    extra_sync_target_relpath="claude",
    rsync_excludes=list(_CLAUDE_CODE_RSYNC_EXCLUDES),
)


class ClaudeCodeSandboxProvider:
    """Workspace provider for the Claude Code engine.

    The base path comes from the injected :class:`WorkspaceConfig`;
    application-prod.yaml leaves it at the sandbox mount, dev/local
    overrides it to the dev's home (`~/.claude_code`). The provider
    does not branch on runtime mode.
    """

    def __init__(self, workspace: cfg.WorkspaceConfig) -> None:
        self._workspace = workspace

    @property
    def engine_type(self) -> str:
        return "claude_code"

    def get_base_path(self) -> str:
        return self._workspace.claude_code_root

    def get_sessions_dir(self) -> str:
        return f"{self._workspace.claude_code_session_root}/projects"

    def get_default_read_only_rules(self) -> list[ReadOnlyRule]:
        return list(_CLAUDE_CODE_DEFAULT_RULES)

    def get_build_plan(self) -> EngineBuildPlan:
        return _CLAUDE_CODE_BUILD_PLAN

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
        base_path = self._workspace.claude_code_root

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

        # No sandbox binding — walk the local filesystem at claude_code_root.
        root = Path(base_path)
        local_target = root / sub_path if sub_path else root
        if local_target.exists() and local_target.is_dir():
            entries = local_target.rglob("*") if recursive else local_target.iterdir()
            for entry in entries:
                rel = str(entry.relative_to(root))
                items.append(DirectoryItem(name=entry.name, path=rel, is_dir=entry.is_dir()))
        else:
            logger.info(
                "[ClaudeCodeSandboxProvider.list_directory] No sandbox binding and "
                "local root %s does not exist; returning empty tree",
                root,
            )
        return items
