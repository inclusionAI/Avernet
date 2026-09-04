"""SandboxProvider 行为单测：聚焦本次新增的 get_sessions_dir、
新增 read-only rules、新增 rsync excludes、以及 EngineBuildPlan
extra_sync_* 是否正确装配。

测试不依赖磁盘——构造 ``WorkspaceConfig`` 时直接传入 prod 默认
路径，避免被 dev 机器上 ``$HOME`` 干扰。
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from agentclaw.community.core.workspace.engines.aicoding import AICodingSandboxProvider
from agentclaw.community.core.workspace.engines.claude_code import ClaudeCodeSandboxProvider
from agentclaw.community.core.workspace.engines.openclaw import OpenClawSandboxProvider
from agentclaw.community.core.workspace.engines.hermes import HermesSandboxProvider
from agentclaw.community.core.workspace.engines import create_engine_sandbox_registry
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
@pytest.mark.parametrize(
    "provider_type",
    (
        OpenClawSandboxProvider,
        ClaudeCodeSandboxProvider,
        AICodingSandboxProvider,
        HermesSandboxProvider,
    ),
)
def test_list_directory_provider_signature_matches_runtime_contract(
    provider_type,
) -> None:
    assert list(inspect.signature(provider_type.list_directory).parameters) == [
        "self",
        "sub_path",
        "recursive",
        "device_fs",
    ]


def _hermes_cross_component_contract() -> dict:
    repo_root = Path(__file__).resolve().parents[6]
    path = (
        repo_root
        / "src/engine/src/engine/community/core/skills/contracts"
        / "hermes_service_build_layout_v1.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


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




@pytest.mark.unit
class TestAICodingProvider:
    def test_get_sessions_dir_uses_session_root_projects(self):
        provider = AICodingSandboxProvider(workspace=_workspace())

        assert provider.get_sessions_dir() == "/home/admin/.aicoding/projects"

    def test_build_plan_uses_aicoding_source_root(self):
        provider = AICodingSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()

        assert plan.engine_type == "aicoding"
        assert plan.source_root_name == ".aicoding"
        assert plan.workspace_subdir == "workspace"
        assert plan.skill_source_relpath == "workspace/skills"
        assert plan.skill_target_relpath == "workspace/skills"
        assert "workspace/.repos/" in plan.rsync_excludes
        assert "workspace/.prewarm_ready.json" in plan.rsync_excludes

    def test_build_plan_excludes_pool_skill_center_symmetric_with_skills_repo(self):
        # skill-center and skills-repo are both external read-only mounts under
        # workspace/skills-pool/; both must be rsync-skipped (not copied) and
        # re-mounted at image startup. Exclude entries must be symmetric:
        # <name> + .<name>* sidecar guard.
        provider = AICodingSandboxProvider(workspace=_workspace())
        excludes = provider.get_build_plan().rsync_excludes

        assert "workspace/skills-pool/skills-repo" in excludes
        assert "workspace/skills-pool/.skills-repo*" in excludes
        assert "workspace/skills-pool/skill-center" in excludes
        assert "workspace/skills-pool/.skill-center*" in excludes

    def test_build_plan_keeps_extra_sync_from_claude(self):
        provider = AICodingSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()

        assert plan.extra_sync_source_relpath == ".claude"
        assert plan.extra_sync_target_relpath == "claude"

    def test_default_read_only_rules_include_workspace_files(self):
        provider = AICodingSandboxProvider(workspace=_workspace())
        rule_paths = {r.path for r in provider.get_default_read_only_rules()}

        for path in (
            "workspace/config/mcporter.json",
            "workspace/.claude/settings.json",
            "workspace/.claude/models.json",
            "workspace/.claude/config.json",
            "workspace/skills-local",
        ):
            assert path in rule_paths

    def test_build_plan_excludes_repo_workspace_dirs_from_template_config(self):
        """aicoding 注入 template_config 声明仓库的 workspace/<repo> 排除。"""
        provider = AICodingSandboxProvider(workspace=_workspace())
        bot = {
            "template_config": {
                "backend_repo": [
                    {"repo_url": "https://code.alipay.com/ASF-Service-Platform/aixcore"}
                ],
                "frontend_repo": [
                    {"repo_url": "https://code.alipay.com/ASF/web-frontend.git"}
                ],
            }
        }
        plan = provider.get_build_plan(bot=bot)
        assert "workspace/aixcore" in plan.rsync_excludes
        assert "workspace/web-frontend" in plan.rsync_excludes

    def test_build_plan_without_bot_keeps_default_excludes(self):
        """无 bot 时 aicoding 不追加额外仓库排除。"""
        provider = AICodingSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()
        assert "workspace/aixcore" not in plan.rsync_excludes

    def test_build_plan_dedupes_repeated_repo(self):
        """同一仓库在多个 key 声明时只追加一次。"""
        provider = AICodingSandboxProvider(workspace=_workspace())
        bot = {
            "template_config": {
                "backend_repo": [{"repo_url": "https://code.alipay.com/ASF/repo.git"}],
                "lib_repo": [{"repo_url": "https://code.alipay.com/ASF/repo"}],
            }
        }
        plan = provider.get_build_plan(bot=bot)
        assert plan.rsync_excludes.count("workspace/repo") == 1

    def test_build_plan_ignores_ac_bots_ext_template_config(self):
        """bot.ext (ac_bots.ext) 不作为仓库来源，即便里面有 template_config 也不读取。"""
        provider = AICodingSandboxProvider(workspace=_workspace())
        bot = {"ext": {"template_config": {"lib_repo": [
            {"repo_url": "https://code.alipay.com/ASF/lib.git"}
        ]}}}
        plan = provider.get_build_plan(bot=bot)
        assert "workspace/lib" not in plan.rsync_excludes


@pytest.mark.unit
class TestHermesProvider:
    def test_build_plan_uses_hermes_runtime_layout(self):
        provider = HermesSandboxProvider(workspace=_workspace())

        plan = provider.get_build_plan()

        assert provider.get_base_path() == "/home/admin/.hermes"
        assert plan.engine_type == "hermes"
        assert plan.source_root_name == ".hermes"
        assert plan.migration_subpath == "hermes"
        assert plan.skill_source_relpath == "skills"
        assert plan.skill_target_relpath == "skills"
        assert "workspace/skills-pool/skills-repo" in plan.rsync_excludes
        assert all("skill-center" not in item for item in plan.rsync_excludes)

    def test_composition_registry_resolves_hermes_without_fallback(self):
        provider = create_engine_sandbox_registry(_workspace()).resolve("hermes")

        assert isinstance(provider, HermesSandboxProvider)

    def test_provider_matches_engine_owned_service_build_contract(self):
        contract = _hermes_cross_component_contract()
        provider = HermesSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()

        assert provider.get_base_path() == contract["engine_root"]
        assert provider.get_sessions_dir() == contract["sessions_root"]
        assert f"{provider.get_base_path()}/{plan.mcp_config_relpath}" == contract[
            "mcp_config"
        ]
        assert f"{provider.get_base_path()}/{plan.skill_target_relpath}" == contract[
            "active_skills"
        ]
        materialized_rules = [
            (
                rule.path
                if rule.path.startswith("/")
                else f"{provider.get_base_path()}/{rule.path}"
            )
            for rule in provider.get_default_read_only_rules()
        ]
        assert materialized_rules == contract["read_only_roots"]
        assert contract["pool_repo"].removeprefix(
            f"{provider.get_base_path()}/"
        ) in plan.rsync_excludes

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

    def test_clawbench_exact_directory_rules_exclude_and_descendants(self):
        # ``workspace/clawbench_results/`` and ``workspace/clawbench_template_generate/``
        # are exact directory rules (trailing slash): hide the directory and descendants.
        provider = OpenClawSandboxProvider(workspace=_workspace())
        # clawbench_results directory and descendants are excluded
        assert provider._is_rsync_excluded("workspace/clawbench_results") is True
        assert provider._is_rsync_excluded("workspace/clawbench_results/sub/file") is True
        # clawbench_template_generate directory and descendants are excluded
        assert provider._is_rsync_excluded("workspace/clawbench_template_generate") is True
        assert provider._is_rsync_excluded("workspace/clawbench_template_generate/sub/file") is True
        # other clawbench_* paths are NOT excluded (no longer wildcard)
        assert provider._is_rsync_excluded("workspace/clawbench_test") is False
        assert provider._is_rsync_excluded("workspace/clawbench_other") is False
        assert provider._is_rsync_excluded("workspace/clawbench-foo") is False


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


# ============================================================================
# Bot-level rsync_excludes override tests (merge mode)
# ============================================================================


@pytest.mark.unit
class TestRsyncExcludesBotOverride:
    """Test Bot-level build_rsync_excludes override with merge semantics."""

    def test_openclaw_uses_default_when_no_override(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()

        # 默认值必须存在
        assert "workspace/memory/" in plan.rsync_excludes
        assert "logs/" in plan.rsync_excludes

    def test_openclaw_merges_bot_override_with_default(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        custom_excludes = ["custom_exclude/", "another_exclude"]
        plan = provider.get_build_plan(build_rsync_excludes_append=custom_excludes)

        # 合并模式：默认值 + 自定义项
        assert "workspace/memory/" in plan.rsync_excludes  # 默认值保留
        assert "logs/" in plan.rsync_excludes  # 默认值保留
        assert "custom_exclude/" in plan.rsync_excludes  # 自定义项追加
        assert "another_exclude" in plan.rsync_excludes  # 自定义项追加

    def test_openclaw_deduplicates_on_merge(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        # 包含与默认值重复的项
        custom_excludes = ["workspace/memory/", "logs/", "new_exclude/"]
        plan = provider.get_build_plan(build_rsync_excludes_append=custom_excludes)

        # 去重：重复项只保留一个
        assert plan.rsync_excludes.count("workspace/memory/") == 1
        assert plan.rsync_excludes.count("logs/") == 1
        assert "new_exclude/" in plan.rsync_excludes

    def test_openclaw_empty_override_keeps_default(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        # 空列表保持默认值不变
        plan = provider.get_build_plan(build_rsync_excludes_append=[])

        assert "workspace/memory/" in plan.rsync_excludes
        assert "logs/" in plan.rsync_excludes

    def test_openclaw_none_override_keeps_default(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan(build_rsync_excludes_append=None)

        assert "workspace/memory/" in plan.rsync_excludes
        assert "logs/" in plan.rsync_excludes

    def test_claude_code_uses_default_when_no_override(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        plan = provider.get_build_plan()

        # 默认值必须存在
        assert "workspace/memory/" in plan.rsync_excludes
        assert "logs/" in plan.rsync_excludes

    def test_claude_code_merges_bot_override(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        custom_excludes = ["workspace/.custom", "/skills-repo-custom"]
        plan = provider.get_build_plan(build_rsync_excludes_append=custom_excludes)

        # 合并模式：默认值 + 自定义项
        assert "workspace/.custom" in plan.rsync_excludes
        assert "/skills-repo-custom" in plan.rsync_excludes
        # 默认值仍保留
        assert "workspace/memory/" in plan.rsync_excludes
        assert "logs/" in plan.rsync_excludes

    def test_claude_code_deduplicates_on_merge(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        # 包含与默认值重复的项
        custom_excludes = ["workspace/memory/", "projects", "new_exclude/"]
        plan = provider.get_build_plan(build_rsync_excludes_append=custom_excludes)

        # 去重：重复项只保留一个
        assert plan.rsync_excludes.count("workspace/memory/") == 1
        assert plan.rsync_excludes.count("projects") == 1
        assert "new_exclude/" in plan.rsync_excludes


@pytest.mark.unit
class TestParseRsyncExcludesFromExt:
    """Test parsing build_rsync_excludes from ac_bots.ext field."""

    def test_none_ext(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        assert parse_build_rsync_excludes_from_ext(None) is None

    def test_empty_ext(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        assert parse_build_rsync_excludes_from_ext({}) is None

    def test_missing_rsync_key(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        assert parse_build_rsync_excludes_from_ext({"other": "value"}) is None

    def test_valid_config(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        ext = {"build_rsync_excludes": ["workspace/memory/", "logs/", "custom/"]}
        result = parse_build_rsync_excludes_from_ext(ext)
        assert result == ["workspace/memory/", "logs/", "custom/"]

    def test_config_with_non_string_items(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        # 非字符串项会被转为字符串
        ext = {"build_rsync_excludes": ["a", 123, "b", 45.6]}
        result = parse_build_rsync_excludes_from_ext(ext)
        assert result == ["a", "123", "b", "45.6"]

    def test_empty_list_returns_none(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        # 空列表被视为 falsy，返回 None（使用默认值）
        assert parse_build_rsync_excludes_from_ext({"build_rsync_excludes": []}) is None

    def test_invalid_type_returns_none(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        # 非列表类型返回 None
        assert parse_build_rsync_excludes_from_ext({"build_rsync_excludes": "invalid"}) is None
        assert parse_build_rsync_excludes_from_ext({"build_rsync_excludes": 123}) is None

    def test_filters_non_string_items(self):
        from agentclaw.community.core.workspace.engines import parse_build_rsync_excludes_from_ext

        # 复杂类型（如 dict、list）被过滤掉
        ext = {"build_rsync_excludes": ["valid", {"invalid": "dict"}, ["nested"], True]}
        result = parse_build_rsync_excludes_from_ext(ext)
        assert result == ["valid"]


@pytest.mark.unit
class TestAICodingProviderChangedLineCoverage:
    def test_base_path_engine_type_and_build_plan_merge(self):
        provider = AICodingSandboxProvider(workspace=_workspace())

        assert provider.engine_type == "aicoding"
        assert provider.get_base_path() == "/home/admin/.aicoding"

        plan = provider.get_build_plan([
            "workspace/memory/",
            "custom-aicoding/",
        ])
        assert plan.engine_type == "aicoding"
        assert plan.rsync_excludes.count("workspace/memory/") == 1
        assert plan.rsync_excludes[-1] == "custom-aicoding/"

    def test_normalize_sub_path_accepts_dot_empty_and_nested_path(self):
        provider = AICodingSandboxProvider(workspace=_workspace())

        assert provider._normalize_sub_path("") == ""
        assert provider._normalize_sub_path(".") == ""
        assert provider._normalize_sub_path("workspace/skills") == "workspace/skills"

    def test_normalize_sub_path_rejects_invalid_paths(self):
        provider = AICodingSandboxProvider(workspace=_workspace())

        with pytest.raises(ValueError):
            provider._normalize_sub_path("bad\x00path")
        with pytest.raises(ValueError):
            provider._normalize_sub_path("/absolute")
        with pytest.raises(ValueError):
            provider._normalize_sub_path("../escape")

    @pytest.mark.asyncio
    async def test_list_directory_with_device_fs_walks_recursively(self):
        base = cfg.WorkspaceConfig().aicoding_root
        provider = AICodingSandboxProvider(workspace=_workspace())
        items = await provider.list_directory(
            recursive=True,
            device_fs=_device_fs({
                base: [
                    {"name": "workspace", "is_dir": True},
                    {"name": "README.md", "is_dir": False},
                    {"name": "", "is_dir": False},
                ],
                f"{base}/workspace": [
                    {"name": "skills", "is_dir": True},
                ],
                f"{base}/workspace/skills": [],
            }),
        )

        assert {item.path for item in items} == {
            "workspace",
            "README.md",
            "workspace/skills",
        }

    @pytest.mark.asyncio
    async def test_list_directory_with_device_fs_non_recursive_keeps_top_level(self):
        base = cfg.WorkspaceConfig().aicoding_root
        provider = AICodingSandboxProvider(workspace=_workspace())
        items = await provider.list_directory(
            recursive=False,
            device_fs=_device_fs({
                base: [
                    {"name": "workspace", "is_dir": True},
                ],
                f"{base}/workspace": [
                    {"name": "nested.txt", "is_dir": False},
                ],
            }),
        )

        assert [item.path for item in items] == ["workspace"]

    @pytest.mark.asyncio
    async def test_list_directory_walks_local_filesystem(self, tmp_path):
        root = tmp_path / ".aicoding"
        workspace_dir = root / "workspace"
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "file.txt").write_text("data")
        provider = AICodingSandboxProvider(
            workspace=cfg.WorkspaceConfig(aicoding_root=str(root)),
        )

        non_recursive = await provider.list_directory(recursive=False)
        recursive = await provider.list_directory(recursive=True)

        assert {item.path for item in non_recursive} == {"workspace"}
        assert {item.path for item in recursive} == {"workspace", "workspace/file.txt"}

    @pytest.mark.asyncio
    async def test_list_directory_missing_local_root_returns_empty(self, tmp_path):
        provider = AICodingSandboxProvider(
            workspace=cfg.WorkspaceConfig(aicoding_root=str(tmp_path / "missing")),
        )

        assert await provider.list_directory() == []


@pytest.mark.unit
class TestGetBuildPlanBotParamCrossEngine:
    """get_build_plan(bot=...) 在所有 provider 上都可用；只有 aicoding 消费它。"""

    def test_openclaw_ignores_bot_repo(self):
        provider = OpenClawSandboxProvider(workspace=_workspace())
        bot = {"template_config": {"backend_repo": [
            {"repo_url": "https://code.alipay.com/ASF/repo.git"}
        ]}}
        plan = provider.get_build_plan(bot=bot)
        assert "workspace/repo" not in plan.rsync_excludes

    def test_claude_code_ignores_bot_repo(self):
        provider = ClaudeCodeSandboxProvider(workspace=_workspace())
        bot = {"template_config": {"backend_repo": [
            {"repo_url": "https://code.alipay.com/ASF/repo.git"}
        ]}}
        plan = provider.get_build_plan(bot=bot)
        assert "workspace/repo" not in plan.rsync_excludes

    def test_calling_without_bot_still_works(self):
        for provider in (
            OpenClawSandboxProvider(workspace=_workspace()),
            ClaudeCodeSandboxProvider(workspace=_workspace()),
            AICodingSandboxProvider(workspace=_workspace()),
        ):
            plan = provider.get_build_plan()
            assert plan.rsync_excludes  # non-empty defaults preserved
