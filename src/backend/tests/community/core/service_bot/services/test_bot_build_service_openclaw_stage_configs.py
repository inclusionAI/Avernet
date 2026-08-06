"""BotBuildService 生成多阶段 OpenClaw 配置文件的单测。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.channel.services.channel_service import OpenClawConfigs
from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


_UNSET = object()


def _make_service(channel_service: object = _UNSET) -> BotBuildService:
    service = BotBuildService.__new__(BotBuildService)
    service._channel_service = MagicMock() if channel_service is _UNSET else channel_service
    service._common_whitelist_service = MagicMock()
    # build() tests in this module isolate stage-config and path selection;
    # artifact permission finalization has its own command-contract coverage.
    service._prepare_artifact_for_build = MagicMock()
    service._finalize_runtime_artifact = MagicMock()
    return service


@pytest.mark.unit
class TestGenerateOpenClawStageConfigs:
    def test_writes_verify_and_online_configs_to_target_dir(self, tmp_path: Path):
        channel_service = MagicMock()
        channel_service.generate_openclaw_configs = AsyncMock(
            return_value=OpenClawConfigs(
                verify='{"stage":"verify"}',
                online='{"stage":"online"}',
                eval='{"stage":"eval","channels":{"dingtalk":{"enabled":false}}}',
            )
        )
        service = _make_service(channel_service)

        ok = service._generate_openclaw_stage_configs(
            bot_id="bot-1",
            owner_id="owner-1",
            target_dir=tmp_path,
        )

        assert ok is True
        assert (tmp_path / "openclaw_verify.json").read_text(
            encoding="utf-8"
        ) == '{"stage":"verify"}'
        assert (tmp_path / "openclaw_online.json").read_text(
            encoding="utf-8"
        ) == '{"stage":"online"}'
        assert (tmp_path / "openclaw_eval.json").read_text(
            encoding="utf-8"
        ) == '{"stage":"eval","channels":{"dingtalk":{"enabled":false}}}'
        channel_service.generate_openclaw_configs.assert_awaited_once_with(
            bot_id="bot-1",
            owner_id="owner-1",
        )

    def test_writes_configs_using_returned_stage_keys(self, tmp_path: Path):
        channel_service = MagicMock()
        channel_service.generate_openclaw_configs = AsyncMock(
            return_value=SimpleNamespace(
                canary='{"stage":"canary"}',
                stable='{"stage":"stable"}',
            )
        )
        service = _make_service(channel_service)
        target_dir = tmp_path / "missing-target"

        ok = service._generate_openclaw_stage_configs(
            bot_id="bot-1",
            owner_id="owner-1",
            target_dir=target_dir,
        )

        assert ok is True
        assert target_dir.is_dir()
        assert (target_dir / "openclaw_canary.json").read_text(
            encoding="utf-8"
        ) == '{"stage":"canary"}'
        assert (target_dir / "openclaw_stable.json").read_text(
            encoding="utf-8"
        ) == '{"stage":"stable"}'
        assert not (target_dir / "openclaw_verify.json").exists()
        assert not (target_dir / "openclaw_online.json").exists()

    def test_skips_empty_config_content(self, tmp_path: Path):
        channel_service = MagicMock()
        channel_service.generate_openclaw_configs = AsyncMock(
            return_value=SimpleNamespace(
                verify='{"stage":"verify"}',
                online='',
                canary=None,
            )
        )
        service = _make_service(channel_service)

        ok = service._generate_openclaw_stage_configs(
            bot_id="bot-1",
            owner_id="owner-1",
            target_dir=tmp_path,
        )

        assert ok is True
        assert (tmp_path / "openclaw_verify.json").read_text(
            encoding="utf-8"
        ) == '{"stage":"verify"}'
        assert not (tmp_path / "openclaw_online.json").exists()
        assert not (tmp_path / "openclaw_canary.json").exists()

    def test_returns_false_when_channel_service_is_not_configured(self, tmp_path: Path):
        service = _make_service(None)

        ok = service._generate_openclaw_stage_configs(
            bot_id="bot-1",
            owner_id="owner-1",
            target_dir=tmp_path,
        )

        assert ok is False
        assert list(tmp_path.iterdir()) == []

    def test_returns_false_when_channel_service_raises(self, tmp_path: Path):
        channel_service = MagicMock()
        channel_service.generate_openclaw_configs = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        service = _make_service(channel_service)

        ok = service._generate_openclaw_stage_configs(
            bot_id="bot-1",
            owner_id="owner-1",
            target_dir=tmp_path,
        )

        assert ok is False
        assert not (tmp_path / "openclaw_verify.json").exists()
        assert not (tmp_path / "openclaw_online.json").exists()
        assert not (tmp_path / "openclaw_eval.json").exists()

    def test_build_invokes_openclaw_config_generation_after_mcp(self, tmp_path: Path):
        @dataclass
        class DummyProvider:
            plan: EngineBuildPlan

            def get_build_plan(self, build_rsync_excludes_append=None) -> EngineBuildPlan:
                return self.plan

        plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name="workspace",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=[],
        )

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=DummyProvider(plan))
        service._get_device_binding_repo = MagicMock(return_value=MagicMock(get_by_id=MagicMock(return_value=None)))
        service._path_factory = MagicMock()
        source_dir = tmp_path / "source"
        source_dir.mkdir(exist_ok=True)
        service._path_factory.get_bot_engine_dir.return_value = source_dir
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)

        import agentclaw.community.core.service_bot.services.bot_build_service as build_module

        original_get_bot_dir = build_module.get_bot_dir
        build_module.get_bot_dir = MagicMock(return_value=tmp_path / "target-root")
        try:
            result = service.build(
                bot={
                    "bot_id": "bot-1",
                    "entity_id": "owner-1",
                    "entity_type": "staff",
                    "device_id": "device-1",
                },
                version=3,
            )
        finally:
            build_module.get_bot_dir = original_get_bot_dir

        target_dir = tmp_path / "target-root" / "3" / "openclaw"
        assert result["success"] is True
        assert result["openclaw_configs_success"] is True
        assert "openclaw_config_paths" not in result
        service._generate_mcp_config.assert_called_once()
        service._generate_openclaw_stage_configs.assert_called_once_with(
            bot_id="bot-1",
            owner_id="owner-1",
            target_dir=target_dir,
        )
        service._finalize_runtime_artifact.assert_called_once_with(target_dir)

    def test_build_marks_failure_when_openclaw_config_generation_fails(self, tmp_path: Path):
        @dataclass
        class DummyProvider:
            plan: EngineBuildPlan

            def get_build_plan(self, build_rsync_excludes_append=None) -> EngineBuildPlan:
                return self.plan

        plan = EngineBuildPlan(
            engine_type="openclaw",
            source_root_name="workspace",
            migration_subpath="openclaw",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=[],
        )

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=DummyProvider(plan))
        service._get_device_binding_repo = MagicMock(return_value=MagicMock(get_by_id=MagicMock(return_value=None)))
        service._path_factory = MagicMock()
        source_dir = tmp_path / "source"
        source_dir.mkdir(exist_ok=True)
        service._path_factory.get_bot_engine_dir.return_value = source_dir
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=False)

        import agentclaw.community.core.service_bot.services.bot_build_service as build_module

        original_get_bot_dir = build_module.get_bot_dir
        build_module.get_bot_dir = MagicMock(return_value=tmp_path / "target-root")
        try:
            result = service.build(
                bot={
                    "bot_id": "bot-1",
                    "entity_id": "owner-1",
                    "entity_type": "staff",
                    "device_id": "device-1",
                },
                version=3,
            )
        finally:
            build_module.get_bot_dir = original_get_bot_dir

        assert result["success"] is False
        assert result["openclaw_configs_success"] is False
        service._finalize_runtime_artifact.assert_not_called()

    def test_build_skips_openclaw_config_generation_for_non_openclaw_engine(
        self, tmp_path: Path
    ):
        @dataclass
        class DummyProvider:
            plan: EngineBuildPlan

            def get_build_plan(self, build_rsync_excludes_append=None) -> EngineBuildPlan:
                return self.plan

        plan = EngineBuildPlan(
            engine_type="claude_code",
            source_root_name=".claude_code",
            migration_subpath="claude_code",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=["projects", "sessions"],
        )

        service = _make_service()
        service._resolve_sandbox_provider = MagicMock(return_value=DummyProvider(plan))
        service._get_device_binding_repo = MagicMock(return_value=MagicMock(get_by_id=MagicMock(return_value=None)))
        service._path_factory = MagicMock()
        source_dir = tmp_path / "source"
        source_dir.mkdir(exist_ok=True)
        service._path_factory.get_bot_engine_dir.return_value = source_dir
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=False)

        import agentclaw.community.core.service_bot.services.bot_build_service as build_module

        original_get_bot_dir = build_module.get_bot_dir
        build_module.get_bot_dir = MagicMock(return_value=tmp_path / "target-root")
        try:
            result = service.build(
                bot={
                    "bot_id": "bot-1",
                    "entity_id": "owner-1",
                    "entity_type": "staff",
                    "device_id": "device-1",
                },
                version=3,
            )
        finally:
            build_module.get_bot_dir = original_get_bot_dir

        assert result["success"] is True
        assert result["openclaw_configs_success"] is True
        service._generate_mcp_config.assert_called_once()
        service._generate_openclaw_stage_configs.assert_not_called()
        service._finalize_runtime_artifact.assert_called_once_with(
            tmp_path / "target-root" / "3" / "claude_code"
        )


@pytest.mark.unit
class TestBuildMigrationPathWhitelist:
    def _build_with_whitelist(
        self,
        tmp_path: Path,
        whitelist_enabled: bool = False,
        whitelist_side_effect: Exception | None = None,
    ):
        @dataclass
        class DummyProvider:
            plan: EngineBuildPlan

            def get_build_plan(self, build_rsync_excludes_append=None) -> EngineBuildPlan:
                return self.plan

        plan = EngineBuildPlan(
            engine_type="claude_code",
            source_root_name=".claude_code",
            migration_subpath="claude_code",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=[],
        )
        whitelist = MagicMock(spec=CommonWhiteListService)
        if whitelist_side_effect is not None:
            whitelist.is_bot_feature_enabled.side_effect = whitelist_side_effect
        else:
            whitelist.is_bot_feature_enabled.return_value = whitelist_enabled

        service = _make_service()
        service._common_whitelist_service = whitelist
        service._resolve_sandbox_provider = MagicMock(return_value=DummyProvider(plan))
        service._get_device_binding_repo = MagicMock(
            return_value=MagicMock(get_by_id=MagicMock(return_value=None))
        )
        service._path_factory = MagicMock()
        source_dir = tmp_path / "source"
        source_dir.mkdir(exist_ok=True)
        service._path_factory.get_bot_engine_dir.return_value = source_dir
        service._migrate_bot_instance = MagicMock(return_value=True)
        service._generate_mcp_config = MagicMock(return_value=True)
        service._generate_openclaw_stage_configs = MagicMock(return_value=True)

        import agentclaw.community.core.service_bot.services.bot_build_service as build_module

        original_get_bot_dir = build_module.get_bot_dir
        build_module.get_bot_dir = MagicMock(return_value=tmp_path / "target-root")
        try:
            result = service.build(
                bot={
                    "bot_id": "bot-1",
                    "entity_id": "owner-1",
                    "entity_type": "staff",
                },
                version=3,
            )
        finally:
            build_module.get_bot_dir = original_get_bot_dir

        return result, whitelist

    def test_build_returns_opt_migration_path_when_whitelist_enabled(self, tmp_path: Path):
        result, whitelist = self._build_with_whitelist(tmp_path, True)

        assert result["migration_path"] == "/opt/nfs/bot-data/3/claude_code"
        whitelist.is_bot_feature_enabled.assert_called_once()
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["business_code"] == "nas_mount"
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["param_code"] == "engine_dir_mount_whitelist"
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["owner_id"] == "owner-1"
        assert whitelist.is_bot_feature_enabled.call_args.kwargs["bot_id"] == "bot-1"

    def test_build_returns_legacy_migration_path_when_whitelist_disabled(self, tmp_path: Path):
        result, _ = self._build_with_whitelist(tmp_path, False)

        assert result["migration_path"] == "/home/admin/nfs/bot-data/3/claude_code"

    def test_build_returns_legacy_migration_path_when_whitelist_raises(self, tmp_path: Path):
        result, whitelist = self._build_with_whitelist(
            tmp_path,
            whitelist_side_effect=RuntimeError("config unavailable"),
        )

        whitelist.is_bot_feature_enabled.assert_called_once()
        assert result["migration_path"] == "/home/admin/nfs/bot-data/3/claude_code"
