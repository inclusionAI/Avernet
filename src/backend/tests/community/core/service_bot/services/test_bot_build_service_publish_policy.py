"""BotBuildService publish 构建资源策略（PublishBuildPolicyConfig）单测。

业务码保持单码道：环境资源差异（部署有没有 NAS staging、设备空 MCP
catalog 是否合法）不靠业务逻辑探宿主机推断，而是由部署配置经 DI 注入的
``PublishBuildPolicyConfig`` 显式声明。默认两个开关都是 True == 生产语
义：NAS 缺失 / 空 catalog 都响亮失败，故障可检测。

覆盖分支：

* _migrate_bot_instance: is_nas 且 source_dir 缺失时，required=True 返
  回 False（pre-policy 响亮失败语义保持），required=False 才显式跳过迁
  移（返回 True），且缺失路径上不发起 chmod。
* _generate_mcp_config: 设备空 stdout 在 required=True 时返回 False，
  required=False 时返回 True。
* _wait_for_device_ready: 就绪探测不携带硬编码 tenant —— 租户回落到
  BaasService 的部署配置；探测超时静默放行（后续 exec_shell 上浮真错）。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan
from agentclaw.community.di.config import PublishBuildPolicyConfig

REQUIRED_POLICY = PublishBuildPolicyConfig()
PERMISSIVE_POLICY = PublishBuildPolicyConfig(
    nas_migration_required=False,
    mcp_catalog_required=False,
)


def _make_plan() -> EngineBuildPlan:
    return EngineBuildPlan(
        engine_type="openclaw",
        source_root_name="openclaw",
        migration_subpath="openclaw",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=["projects"],
    )


def _make_service(policy: PublishBuildPolicyConfig) -> BotBuildService:
    """绕过 @inject 构造；只塞本组测试直接依赖的字段。"""
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    service._baas_service = MagicMock()
    service._publish_policy = policy
    return service


def _exec_result(stdout: str = "") -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.stderr = ""
    return result


@pytest.mark.unit
class TestMigrateNasPolicy:
    def test_required_missing_source_fails_loud(self, tmp_path: Path):
        # 生产语义：声明依赖 NAS 的部署遇到缺失的 source 必须
        # 返回 False（构建失败可观测），而不是静默宣称迁移成功。
        service = _make_service(REQUIRED_POLICY)
        service._run_local_command = MagicMock()

        missing_source = tmp_path / "nas-root" / "bot-a"
        ok = service._migrate_bot_instance(
            device_id="device-1",
            source_dir=missing_source,
            target_dir=tmp_path / "target",
            version_str="v1",
            is_nas=True,
            nas_storage_id=tmp_path / "nas-root",
            build_plan=_make_plan(),
            provider=MagicMock(),
        )
        assert ok is False

    def test_required_missing_source_runs_no_chmod(self, tmp_path: Path):
        # chmod 守卫：staging 根缺失时不发起任何 chmod 命令，
        # 失败语义由 source 检查给出，与 chmod 无关的错误不抢先。
        service = _make_service(REQUIRED_POLICY)
        service._run_local_command = MagicMock()

        service._migrate_bot_instance(
            device_id="device-1",
            source_dir=tmp_path / "nas-root" / "bot-a",
            target_dir=tmp_path / "target",
            version_str="v1",
            is_nas=True,
            nas_storage_id=tmp_path / "nas-root",
            build_plan=_make_plan(),
            provider=MagicMock(),
        )
        service._run_local_command.assert_not_called()

    def test_optional_missing_source_skips_migration(self, tmp_path: Path):
        # 声明无 NAS 资源的部署（required=False）：缺失即显式跳过迁移，
        # 跳过决定来自配置声明，而不是对宿主机文件系统的探测。
        service = _make_service(PERMISSIVE_POLICY)
        service._run_local_command = MagicMock()

        ok = service._migrate_bot_instance(
            device_id="device-1",
            source_dir=tmp_path / "nas-root" / "bot-a",
            target_dir=tmp_path / "target",
            version_str="v1",
            is_nas=True,
            nas_storage_id=tmp_path / "nas-root",
            build_plan=_make_plan(),
            provider=MagicMock(),
        )
        assert ok is True


@pytest.mark.unit
class TestMcpCatalogPolicy:
    def test_required_empty_catalog_fails_build(self, tmp_path: Path):
        # 生产语义：设备空 MCP catalog 视作构建失败（隐藏 mcporter
        # 损坏或 device shell 异常）。
        service = _make_service(REQUIRED_POLICY)
        service._device_service.exec_shell_new = MagicMock(
            return_value=_exec_result("")
        )

        ok = service._generate_mcp_config(
            device_id="device-1",
            target_dir=tmp_path / "target",
            build_plan=_make_plan(),
        )
        assert ok is False

    def test_optional_empty_catalog_continues(self, tmp_path: Path):
        # 声明空 catalog 合法的部署：构建继续，不生成远端 MCP 列表。
        service = _make_service(PERMISSIVE_POLICY)
        service._device_service.exec_shell_new = MagicMock(
            return_value=_exec_result("")
        )

        ok = service._generate_mcp_config(
            device_id="device-1",
            target_dir=tmp_path / "target",
            build_plan=_make_plan(),
        )
        assert ok is True


@pytest.mark.unit
class TestWaitForDeviceReady:
    def test_ready_probe_uses_no_hardcoded_tenant(self):
        # 就绪探测不携带硬编码 tenant —— 租户回落到 BaasService 的
        # 部署配置（BaasConfig.tenant）。
        service = _make_service(REQUIRED_POLICY)
        service._baas_service.get_ws_info_by_bot_uuid = MagicMock(
            return_value={"ws": "ready"}
        )

        service._wait_for_device_ready("device-1")

        service._baas_service.get_ws_info_by_bot_uuid.assert_called_once_with(
            bot_uuid="device-1"
        )

    def test_timeout_proceeds_silently(self):
        # 一直 404/异常时到超时后静默返回，错误留给后续真实调用上浮。
        service = _make_service(REQUIRED_POLICY)
        service._baas_service.get_ws_info_by_bot_uuid = MagicMock(
            side_effect=Exception("404 BOT_NOT_FOUND")
        )

        # 不抛异常即通过
        service._wait_for_device_ready(
            "device-1",
            timeout_seconds=0.05,
            poll_interval_seconds=0.01,
        )