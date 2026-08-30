"""测试 BotBuildService.build() 从 bot.ext 读取并传递 rsync excludes 配置。

覆盖 bot_build_service.py 第 224-229 行的变更：
- 从 bot.ext 解析 build_rsync_excludes 配置
- 调用 parse_build_rsync_excludes_from_ext(ext)
- 将解析结果传递给 provider.get_build_plan(build_rsync_excludes_append=...)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
    BotBuildServiceError,
)
from agentclaw.community.core.service_bot.services.deploy.service_skills_manifest import (
    ResolvedSharedCorpusDelivery,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


def _make_service() -> BotBuildService:
    """构造一个 BotBuildService 实例，绕过 @inject。

    只设置 build 方法直接依赖的字段。
    """
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    return service


def _center_delivery(runtime_path: str) -> ResolvedSharedCorpusDelivery:
    return ResolvedSharedCorpusDelivery(
        corpus="center",
        runtime_path=runtime_path,
        store_prefix="aidesktop/aidesktop_dev/bolt_shared/skills-center",
        layout_contract_version="skills-pool-p3-v1",
    )


@pytest.mark.unit
def test_engine_evidence_adds_center_corpus_to_snapshot_excludes() -> None:
    provider = MagicMock()
    provider.get_base_path.return_value = "/home/admin/.openclaw"
    plan = EngineBuildPlan(
        engine_type="openclaw",
        source_root_name=".openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=["logs/"],
    )

    updated = BotBuildService._apply_shared_corpus_excludes(
        build_plan=plan,
        provider=provider,
        shared_corpora=(
            _center_delivery(
                "/home/admin/.openclaw/workspace/skills-pool/skill-center"
            ),
        ),
    )

    assert updated.rsync_excludes == [
        "logs/",
        "workspace/skills-pool/skill-center",
    ]
    assert plan.rsync_excludes == ["logs/"]


@pytest.mark.unit
def test_center_corpus_outside_snapshot_root_fails_closed() -> None:
    provider = MagicMock()
    provider.get_base_path.return_value = "/home/admin/.openclaw"
    plan = EngineBuildPlan(
        engine_type="openclaw",
        source_root_name=".openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=[],
    )

    with pytest.raises(BotBuildServiceError, match="outside"):
        BotBuildService._apply_shared_corpus_excludes(
            build_plan=plan,
            provider=provider,
            shared_corpora=(
                _center_delivery("/home/admin/.aicoding/workspace/skill-center"),
            ),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    (
        "base_path",
        "extra_source",
        "extra_target",
        "active_runtime_path",
        "expected",
    ),
    (
        (
            "/home/admin/.openclaw",
            "",
            "",
            "/home/admin/.openclaw/workspace/skills",
            "workspace/skills",
        ),
        (
            "/home/admin/.claude_code",
            ".claude",
            "claude",
            "/home/admin/.claude/skills",
            "claude/skills",
        ),
        (
            "/home/admin/.aicoding",
            ".claude",
            "claude",
            "/home/admin/.claude/skills",
            "claude/skills",
        ),
        (
            "/home/admin/.hermes",
            "",
            "",
            "/home/admin/.hermes/skills",
            "skills",
        ),
    ),
)
def test_engine_active_evidence_maps_through_existing_snapshot_sources(
    base_path,
    extra_source,
    extra_target,
    active_runtime_path,
    expected,
) -> None:
    provider = MagicMock()
    provider.get_base_path.return_value = base_path
    plan = EngineBuildPlan(
        engine_type="engine",
        source_root_name=".engine",
        migration_subpath="engine",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=[],
        extra_sync_source_relpath=extra_source,
        extra_sync_target_relpath=extra_target,
    )

    assert BotBuildService._active_skill_snapshot_path(
        provider=provider,
        build_plan=plan,
        shared_corpora=(
            _center_delivery(
                f"{base_path}/workspace/skills-pool/skill-center"
            ),
        ),
        active_runtime_path=active_runtime_path,
    ) == expected


@pytest.mark.unit
class TestBotBuildServiceRsyncExcludesConfig:
    """测试 Bot 级别 rsync excludes 配置的读取和传递。"""

    def test_build_passes_ext_config_to_provider(self):
        """验证 build() 方法从 bot.ext 读取配置并传递给 provider。"""
        # 准备测试数据
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            "ext": {
                "build_rsync_excludes": ["custom_exclude/", "another_exclude"]
            },
        }

        # Mock provider 和其返回的 build_plan
        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        # Mock BotBuildService
        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        # 执行 build
        try:
            service.build(bot, version=1)
        except Exception:
            # build() 方法可能因为 mock 不完整而失败
            # 但我们只关心 provider.get_build_plan 的调用
            pass

        # 验证 provider.get_build_plan 被正确调用
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]

        # 验证传递的参数是解析后的 rsync excludes 列表
        assert "build_rsync_excludes_append" in call_kwargs
        assert call_kwargs["build_rsync_excludes_append"] == [
            "custom_exclude/",
            "another_exclude",
        ]

    def test_build_handles_none_ext(self):
        """验证 bot.ext 为 None 时使用默认值。"""
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            "ext": None,  # ext 为 None
        }

        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        try:
            service.build(bot, version=1)
        except Exception:
            pass

        # 验证传递 None 给 get_build_plan
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]
        assert call_kwargs["build_rsync_excludes_append"] is None

    def test_build_handles_missing_ext_key(self):
        """验证 bot 缺少 ext 字段时使用默认值。"""
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            # 缺少 ext 字段
        }

        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        try:
            service.build(bot, version=1)
        except Exception:
            pass

        # 验证传递 None 给 get_build_plan
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]
        assert call_kwargs["build_rsync_excludes_append"] is None

    def test_build_empty_ext_build_rsync_excludes(self):
        """验证 ext.build_rsync_excludes 为空列表时使用默认值。"""
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            "ext": {
                "build_rsync_excludes": []  # 空列表
            },
        }

        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        try:
            service.build(bot, version=1)
        except Exception:
            pass

        # 空列表被视为 falsy，parse_build_rsync_excludes_from_ext 返回 None
        mock_provider.get_build_plan.assert_called_once()
        call_kwargs = mock_provider.get_build_plan.call_args[1]
        assert call_kwargs["build_rsync_excludes_append"] is None

    def test_build_resolve_sandbox_provider_uses_resolver_for_template_routing(self):
        bot = {
            "bot_id": "test-bot-id",
            "entity_id": "test-entity-id",
            "entity_type": "staff",
            "device_id": "test-device-id",
            "active_engine": "claude_code",
            "template_type": "generalCC",
        }

        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="aicoding",
            source_root_name=".aicoding",
            migration_subpath="aicoding",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan

        service = _make_service()
        service._sandbox_registry = MagicMock()
        service._sandbox_registry.resolve.return_value = mock_provider
        service._bot_repository = MagicMock()
        service._bot_repository.get_by_id_and_owner.return_value = bot
        service._bot_repository.get_by_id.return_value = bot
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")

        try:
            service.build(bot, version=1)
        except Exception:
            pass

        service._sandbox_registry.resolve.assert_any_call("aicoding")


def test_resolve_sandbox_provider_retries_repo_resolved_engine_before_default():
    service = BotBuildService.__new__(BotBuildService)
    service._bot_repository = MagicMock()
    service._bot_repository.get_by_id_and_owner.return_value = {
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "active_engine": "claude_code",
        "template_type": "normalCC",
    }
    service._bot_repository.get_by_id.return_value = service._bot_repository.get_by_id_and_owner.return_value

    default_provider = MagicMock(name="default_provider")
    repo_provider = MagicMock(name="repo_provider")
    service._sandbox_registry = MagicMock()
    service._sandbox_registry.resolve.side_effect = [RuntimeError("missing routed provider"), repo_provider]

    provider = service._resolve_sandbox_provider({
        "bot_id": "bot-1",
        "owner_id": "owner-1",
        "active_engine": "unknown_engine",
    })

    assert provider is repo_provider
    assert service._sandbox_registry.resolve.call_args_list == [
        (("unknown_engine",),),
        (("claude_code",),),
    ]
    default_provider.assert_not_called()


def test_resolve_sandbox_provider_falls_back_to_default_when_retry_fails():
    service = BotBuildService.__new__(BotBuildService)
    service._bot_repository = MagicMock()
    service._bot_repository.get_by_id_and_owner.side_effect = RuntimeError("repo unavailable")
    service._bot_repository.get_by_id.side_effect = RuntimeError("repo unavailable")

    default_provider = MagicMock(name="default_provider")
    service._sandbox_registry = MagicMock()
    service._sandbox_registry.resolve.side_effect = [
        RuntimeError("missing first provider"),
        default_provider,
    ]

    provider = service._resolve_sandbox_provider({
        "bot_id": "bot-1",
        "entity_id": "owner-1",
        "active_engine": "unknown_engine",
    })

    assert provider is default_provider
    assert service._sandbox_registry.resolve.call_args_list == [
        (("unknown_engine",),),
        (("openclaw",),),
    ]


def test_known_hermes_provider_never_falls_back_to_openclaw():
    service = BotBuildService.__new__(BotBuildService)
    service._bot_repository = None
    service._sandbox_registry = MagicMock()
    service._sandbox_registry.resolve.side_effect = RuntimeError("hermes missing")

    with pytest.raises(RuntimeError, match="hermes missing"):
        service._resolve_sandbox_provider(
            {"active_engine": "hermes", "bot_id": "bot-1"}
        )

    service._sandbox_registry.resolve.assert_called_once_with("hermes")


def test_build_uses_original_active_engine_for_nas_source_bucket_when_routed_to_aicoding():
    bot = {
        "bot_id": "20260811_lklnq6d0",
        "entity_id": "382716",
        "entity_type": "staff",
        "device_id": "device-1",
        "active_engine": "claude_code",
        "template_type": "generalCC",
    }

    mock_provider = MagicMock()
    mock_provider.get_build_plan.return_value = EngineBuildPlan(
        engine_type="aicoding",
        source_root_name=".aicoding",
        migration_subpath="aicoding",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=[],
    )

    service = _make_service()
    service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
    service._migrate_bot_instance = MagicMock(return_value=True)
    service._generate_mcp_config = MagicMock(return_value=True)
    service._generate_openclaw_stage_configs = MagicMock(return_value=True)
    service._get_migration_path_base = MagicMock(return_value="/fake/path")

    with patch(
        "agentclaw.community.core.service_bot.services.bot_build_service.get_bot_nas_dir",
        return_value=Path("/home/admin/.merge_nas/pre_staff_382716_claude_code_20260811_lklnq6d0"),
    ) as mock_get_bot_nas_dir:
        result = service.build(bot, version=2)

    assert result["success"] is True
    mock_get_bot_nas_dir.assert_called_once_with(
        entity_id="382716",
        bot_id="20260811_lklnq6d0",
        engine_type="claude_code",
        entity_type="staff",
    )
    service._migrate_bot_instance.assert_called_once()
    migrate_kwargs = service._migrate_bot_instance.call_args.kwargs
    assert migrate_kwargs["source_dir"] == Path(
        "/home/admin/.merge_nas/pre_staff_382716_claude_code_20260811_lklnq6d0/.aicoding"
    )
    assert migrate_kwargs["target_dir"].parts[-3:] == (
        "20260811_lklnq6d0",
        "2",
        "aicoding",
    )


# ---- repo-exclude integration via get_build_plan(bot=...) ----


@pytest.mark.unit
class TestBotBuildServiceBotParamForwarding:
    """验证 build()/restore_draft() 把 bot 透传给 get_build_plan，
    repo 排除由 aicoding provider 内部消费，build 服务不感知。"""

    def _setup(self):
        mock_provider = MagicMock()
        mock_build_plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name=".openclaw",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["workspace/memory/", "logs/"],
        )
        mock_provider.get_build_plan.return_value = mock_build_plan
        service = BotBuildService.__new__(BotBuildService)
        service._device_service = MagicMock()
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)
        service._get_migration_path_base = MagicMock(return_value="/fake/path")
        service._resolve_sandbox_provider = MagicMock(return_value=mock_provider)
        return service, mock_provider

    def test_build_forwards_bot_to_get_build_plan(self):
        bot = {
            "bot_id": "b", "entity_id": "e", "entity_type": "staff",
            "device_id": "d",
            "ext": {"build_rsync_excludes": ["custom/"]},
            "template_config": {"backend_repo": [
                {"repo_url": "https://code.alipay.com/ASF/repo.git"}
            ]},
        }
        service, mock_provider = self._setup()
        try:
            service.build(bot, version=1)
        except Exception:
            pass
        mock_provider.get_build_plan.assert_called_once()
        assert mock_provider.get_build_plan.call_args.kwargs["bot"] is bot

    def test_build_forwards_bot_even_without_repos(self):
        bot = {"bot_id": "b", "entity_id": "e", "entity_type": "staff",
               "device_id": "d"}
        service, mock_provider = self._setup()
        try:
            service.build(bot, version=1)
        except Exception:
            pass
        assert mock_provider.get_build_plan.call_args.kwargs["bot"] is bot
