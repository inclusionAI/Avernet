"""SandboxProvider 行为单测：聚焦本次新增的 get_sessions_dir、
新增 read-only rules、新增 rsync excludes、以及 EngineBuildPlan
extra_sync_* 是否正确装配。

测试不依赖磁盘——构造 ``WorkspaceConfig`` 时直接传入 prod 默认
路径，避免被 dev 机器上 ``$HOME`` 干扰。
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.workspace.engines.claude_code import ClaudeCodeSandboxProvider
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.di import config as cfg


def _workspace() -> cfg.WorkspaceConfig:
    return cfg.WorkspaceConfig()


def _device_fs(mapping: dict[str, list[dict]]):
    """Build a device-fs double whose ``list_dir`` is keyed by the requested path.

    The providers expand recursion client-side with one-level (non-recursive)
    list calls, so the stub ignores the ``recursive`` flag and just returns the
    canned listing registered for ``target_path``.
    """

    class _FS:
        async def list_dir(self, target_path, recursive=False):  # noqa: ANN001
            return mapping.get(target_path, [])

    return _FS()


@pytest.mark.unit
class TestOpenClawProvider:
    def test_get_sessions_dir_is_base_path_agents(self):
        # openclaw 的 sessions 目录保持历史约定：紧跟 base_path 下的 agents/
        provider = OpenClawSandboxProvider(workspace=_workspace())

        assert provider.get_sessions_dir() == "/home/admin/.openclaw/agents"

    def test_get_sessions_dir_is_base_path_relative(self):
        # 不变性：openclaw 的 sessions_dir 必须以 base_path 为前缀，
        # 防止 BaaS 解耦后两者发生独立漂移。
        provider = OpenClawSandboxProvider(workspace=_workspace())

        assert provider.get_sessions_dir().startswith(provider.get_base_path())

    def test_build_plan_has_no_extra_sync(self):
        # openclaw 不需要 extra sync——必须保留默认空，
        # 否则 BotBuildService 会误触发额外的 rsync。
        provider = OpenClawSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()

        assert plan.extra_sync_source_relpath == ""
        assert plan.extra_sync_target_relpath == ""

    def test_build_snapshot_excludes_pool_shared_repo(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())

        assert (
            "workspace/skills-pool/skills-repo"
            in provider.get_build_plan().rsync_excludes
        )

    def test_default_read_only_rules_include_skills_local(self):
        # 验证 workspace/skills/skills-local 路径在只读规则中
        # （修正了历史路径 workspace/skills-local 的错误）
        provider = OpenClawSandboxProvider(workspace=_workspace())
        rule_paths = {r.path for r in provider.get_default_read_only_rules()}

        assert "workspace/skills/skills-local" in rule_paths
        # 确保旧路径不再存在
        assert "workspace/skills-local" not in rule_paths


@pytest.mark.unit
class TestClaudeCodeProvider:
    def test_get_sessions_dir_uses_session_root_projects(self):
        # claude_code 的 sessions 目录由独立的 claude_code_session_root
        # 解析，固定以 /projects 结尾——与 sandbox 内
        # ~/.claude/projects 一致，不复用 base_path 下的 /agents 约定。
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())

        assert provider.get_sessions_dir() == "/home/admin/.claude/projects"

    def test_get_sessions_dir_decoupled_from_base_path(self):
        # 设计意图：sessions 目录与 base_path 解耦，使用独立的
        # session_root 配置。覆盖任一字段都要能独立生效。
        custom = cfg.WorkspaceConfig(
            claude_code_root="/tmp/cc-base",
            claude_code_session_root="/tmp/cc-session",
        )
        provider = ClaudeCodeSandboxProvider(workspace=custom)

        assert provider.get_base_path() == "/tmp/cc-base"
        assert provider.get_sessions_dir() == "/tmp/cc-session/projects"

    def test_build_plan_extra_sync_targets_dot_claude(self):
        # claude_code 需要把 source 父目录下的 .claude 同步到 target 下的 claude/，
        # 用于把会话产物从源 Bot 拷贝到新 Bot。
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()

        assert plan.extra_sync_source_relpath == ".claude"
        assert plan.extra_sync_target_relpath == "claude"

    def test_build_snapshot_excludes_pool_shared_repo(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())

        assert (
            "workspace/skills-pool/skills-repo"
            in provider.get_build_plan().rsync_excludes
        )

    def test_default_read_only_rules_include_settings_models_config(self):
        # 这次新增的只读规则覆盖了 settings.json / models.json / config.json
        # 在 workspace/.claude/ 与 base 根目录两个位置，确保 sandbox 内
        # claude_code 关键配置无法被 Bot 覆盖写。
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        rule_paths = {r.path for r in provider.get_default_read_only_rules()}

        for path in (
            "workspace/.claude/settings.json",
            "workspace/.claude/models.json",
            "workspace/.claude/config.json",
            "settings.json",
            "models.json",
            "config.json",
        ):
            assert path in rule_paths, f"missing read-only rule: {path}"

    def test_default_read_only_rules_no_longer_include_claude_code_json(self):
        # 历史规则 claude_code.json 已被显式拆分为 settings/models/config 三类，
        # 不应再出现在默认规则里。
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        rule_paths = {r.path for r in provider.get_default_read_only_rules()}

        assert "claude_code.json" not in rule_paths

    def test_rsync_excludes_cover_new_session_artefacts(self):
        # claude_code 的会话产物（projects/sessions/shell-snapshots 等）
        # 不应被主 rsync 拷贝，必须出现在 excludes 里。
        # skills-repo 加 leading / 锚定 source 根:主 rsync(source=.claude_code/)
        # 仍排掉根下的 OSS 挂载点;extra_sync(source=.claude/)不再误伤
        # .claude/skills/skills-repo 这条转接软链。
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        excludes = set(provider.get_build_plan().rsync_excludes)

        for pattern in (
            "workspace/.claude",
            "projects",
            "sessions",
            "shell-snapshots",
            ".last-cleanup",
            "backups",
            "telemetry",
            "session-env",
            "/skills-repo",
        ):
            assert pattern in excludes, f"missing rsync exclude: {pattern}"

        # Regression guard: 无锚定的 "skills-repo" 不能再回归出现 —
        # 它会让 extra_sync (source=.claude/) 任意层级匹配,把
        # .claude/skills/skills-repo 这条转接软链误伤,导致预发态容器内
        # 所有 skill 软链 dangling。详见 PR #3000。
        assert "skills-repo" not in excludes, \
            'unanchored "skills-repo" exclude regressed — must use "/skills-repo"'


_OPENCLAW_ROOT = cfg.WorkspaceConfig().openclaw_root
_CLAUDE_CODE_ROOT = cfg.WorkspaceConfig().claude_code_root


@pytest.mark.unit
class TestOpenClawNormalizeSubPath:
    def test_empty_returns_empty(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._normalize_sub_path("") == ""

    def test_dot_collapses_to_empty(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._normalize_sub_path("./") == ""

    def test_null_byte_is_rejected(self):
        # A NUL byte can smuggle a truncated path past downstream checks; the
        # provider must reject it before it reaches the sandbox API.
        provider = OpenClawSandboxProvider(workspace=_workspace())
        with pytest.raises(ValueError):
            provider._normalize_sub_path("work\x00space")

    def test_parent_traversal_is_rejected(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        with pytest.raises(ValueError):
            provider._normalize_sub_path("../escape")

    def test_absolute_path_is_rejected(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        with pytest.raises(ValueError):
            provider._normalize_sub_path("/etc/passwd")


@pytest.mark.unit
class TestOpenClawIsRsyncExcluded:
    def test_empty_path_not_excluded(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._is_rsync_excluded("") is False
        assert provider._is_rsync_excluded("/") is False

    def test_plain_directory_rule_excludes_dir_and_descendants(self):
        # ``memory/`` is a trailing-slash directory rule: it must hide the
        # directory itself and everything beneath it.
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._is_rsync_excluded("memory") is True
        assert provider._is_rsync_excluded("memory/notes/today.md") is True

    def test_wildcard_directory_rule(self):
        # ``agents/*/sessions/`` — wildcard segment plus descendant hiding.
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._is_rsync_excluded("agents/abc/sessions") is True
        assert provider._is_rsync_excluded("agents/abc/sessions/s1/log") is True

    def test_non_slash_prefix_rule_hides_descendants(self):
        # ``workspace/skills/.skills-repo*`` has no trailing slash but still
        # needs to hide descendants of a matched directory.
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._is_rsync_excluded("workspace/skills/.skills-repo-x") is True
        assert provider._is_rsync_excluded("workspace/skills/.skills-repo-x/inner") is True

    def test_exact_non_slash_dir_rule_hides_descendants_via_ancestor(self):
        # ``workspace/skills/skills-repo`` (exact, no wildcard, no trailing
        # slash): a child path does not match the rule directly, so descendant
        # hiding must come from the ancestor check.
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._is_rsync_excluded("workspace/skills/skills-repo") is True
        assert provider._is_rsync_excluded("workspace/skills/skills-repo/sub/file") is True

    def test_exact_file_rule(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._is_rsync_excluded("update-check.json") is True

    def test_unmatched_path_not_excluded(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        assert provider._is_rsync_excluded("workspace/README.md") is False


@pytest.mark.unit
class TestOpenClawListDirectorySandbox:
    @pytest.mark.asyncio
    async def test_recursive_walk_filters_excluded_and_recurses(self):
        base = _OPENCLAW_ROOT
        mapping = {
            base: [
                {"name": "workspace", "is_dir": True},
                {"name": "memory", "is_dir": True},        # excluded (memory/)
                {"name": "file.txt", "is_dir": False},
                {"name": "", "is_dir": False},             # nameless -> skipped
            ],
            f"{base}/workspace": [
                {"name": "config", "is_dir": True},
                {"name": "memory", "is_dir": True},        # excluded (workspace/memory/)
                {"name": "emptydir", "is_dir": True},
            ],
            f"{base}/workspace/config": [
                {"name": "mcporter.json", "is_dir": False},  # excluded exact rule
            ],
            f"{base}/workspace/emptydir": [],                # hits empty-return branch
        }
        provider = OpenClawSandboxProvider(workspace=_workspace())
        items = await provider.list_directory(recursive=True, device_fs=_device_fs(mapping))

        paths = {i.path for i in items}
        assert paths == {"workspace", "file.txt", "workspace/config", "workspace/emptydir"}
        # excluded entries never surface
        assert "memory" not in paths
        assert "workspace/memory" not in paths
        assert "workspace/config/mcporter.json" not in paths

    @pytest.mark.asyncio
    async def test_non_recursive_lists_only_top_level(self):
        base = _OPENCLAW_ROOT
        mapping = {
            base: [
                {"name": "workspace", "is_dir": True},
                {"name": "file.txt", "is_dir": False},
            ],
        }
        provider = OpenClawSandboxProvider(workspace=_workspace())
        items = await provider.list_directory(recursive=False, device_fs=_device_fs(mapping))

        assert {i.path for i in items} == {"workspace", "file.txt"}


@pytest.mark.unit
class TestClaudeCodeNormalizeSubPath:
    def test_null_byte_is_rejected(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        with pytest.raises(ValueError):
            provider._normalize_sub_path("work\x00space")

    def test_parent_traversal_is_rejected(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        with pytest.raises(ValueError):
            provider._normalize_sub_path("../escape")

    def test_dot_collapses_to_empty(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        assert provider._normalize_sub_path(".") == ""


@pytest.mark.unit
class TestClaudeCodeListDirectorySandbox:
    @pytest.mark.asyncio
    async def test_recursive_walk_collects_tree(self):
        base = _CLAUDE_CODE_ROOT
        mapping = {
            base: [
                {"name": "workspace", "is_dir": True},
                {"name": "file.txt", "is_dir": False},
                {"name": "", "is_dir": False},     # nameless -> skipped
            ],
            f"{base}/workspace": [
                {"name": "deep", "is_dir": True},
            ],
            f"{base}/workspace/deep": [],          # empty-return branch
        }
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        items = await provider.list_directory(recursive=True, device_fs=_device_fs(mapping))

        # claude_code provider does not apply rsync-exclude filtering.
        assert {i.path for i in items} == {"workspace", "file.txt", "workspace/deep"}

    @pytest.mark.asyncio
    async def test_empty_sandbox_returns_empty_list(self):
        base = _CLAUDE_CODE_ROOT
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        items = await provider.list_directory(recursive=True, device_fs=_device_fs({base: []}))

        assert items == []
