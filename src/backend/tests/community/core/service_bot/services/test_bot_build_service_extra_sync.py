"""BotBuildService._migrate_bot_instance 中 extra sync 分支的单测。

本次为 claude_code 引擎引入 EngineBuildPlan.extra_sync_* 后，
``_migrate_bot_instance`` 在主 rsync 之后会按 build_plan 决定是否再发起
一次额外的 rsync（把 source 父目录下的子目录拷贝到 target 下的另一个
子目录）。这里覆盖三个关键分支：

* extra_sync_* 为空：跳过额外 rsync（openclaw 等老引擎的行为）
* extra_sync_* 配置 + 源目录存在：执行额外 rsync，命令格式正确
* extra_sync_* 配置 + 源目录缺失：raise BotBuildMigrationError
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildMigrationError,
    BotBuildService,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan


def _make_plan(
    *,
    extra_src: str = "",
    extra_tgt: str = "",
    excludes: list[str] | None = None,
) -> EngineBuildPlan:
    return EngineBuildPlan(
        engine_type="claude_code",
        source_root_name=".claude_code",
        migration_subpath="claude_code",
        workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills",
        skill_target_relpath="workspace/skills",
        rsync_excludes=excludes or ["projects", "sessions"],
        extra_sync_source_relpath=extra_src,
        extra_sync_target_relpath=extra_tgt,
    )


def _make_service() -> BotBuildService:
    """构造一个 BotBuildService 实例，绕过 @inject。

    只设置 _migrate_bot_instance 直接依赖的字段；其他 baas / repo
    在该方法路径上不会被调用。
    """
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    return service


def _seed_dirs(tmp_path: Path, *, with_extra_source: bool = False) -> tuple[Path, Path]:
    """在 tmp_path 下准备 source / target / 可选的 extra source 目录。"""
    bot_parent = tmp_path / "bots"
    source_dir = bot_parent / "source-bot"
    target_dir = tmp_path / "target-bot"
    source_dir.mkdir(parents=True)
    target_dir.mkdir(parents=True)
    if with_extra_source:
        (bot_parent / ".claude").mkdir()
    return source_dir, target_dir


@pytest.mark.unit
class TestMigrateBotInstanceExtraSync:
    def test_skips_extra_sync_when_plan_has_no_extra(self, tmp_path: Path):
        # openclaw 等引擎不配置 extra_sync_*，本路径上只应触发一次
        # rsync（command_name="rsync"），不应出现 "rsync (extra)"。
        service = _make_service()
        service._run_local_command = MagicMock(return_value=None)

        source_dir, target_dir = _seed_dirs(tmp_path)
        plan = _make_plan()  # extra_sync_* 默认空

        ok = service._migrate_bot_instance(
            device_id="dev1",
            source_dir=source_dir,
            target_dir=target_dir,
            version_str="v1",
            is_nas=True,
            nas_storage_id=str(source_dir),
            build_plan=plan,
            provider=MagicMock(),
        )

        assert ok is True
        command_names = [
            kwargs.get("command_name") or args[1]
            for args, kwargs in (
                (c.args, c.kwargs) for c in service._run_local_command.call_args_list
            )
        ]
        assert "rsync" in command_names
        assert "rsync (extra)" not in command_names

    def test_runs_extra_sync_when_plan_configures_and_source_exists(
        self, tmp_path: Path
    ):
        # claude_code 走 extra sync：source_dir.parent/.claude → target_dir/claude，
        # 命令使用与主 rsync 一致的 -av --delete + excludes 模板。
        service = _make_service()
        service._run_local_command = MagicMock(return_value=None)

        source_dir, target_dir = _seed_dirs(tmp_path, with_extra_source=True)
        plan = _make_plan(
            extra_src=".claude",
            extra_tgt="claude",
            excludes=["projects", "sessions"],
        )

        ok = service._migrate_bot_instance(
            device_id="dev1",
            source_dir=source_dir,
            target_dir=target_dir,
            version_str="v1",
            is_nas=True,
            nas_storage_id=str(source_dir),
            build_plan=plan,
            provider=MagicMock(),
        )

        assert ok is True

        extra_calls = [
            call
            for call in service._run_local_command.call_args_list
            if call.kwargs.get("command_name") == "rsync (extra)"
        ]
        assert len(extra_calls) == 1, (
            f"expected exactly one extra rsync, got {service._run_local_command.call_args_list}"
        )

        extra_cmd = extra_calls[0].kwargs["cmd"]
        assert extra_cmd[:4] == ["sudo", "rsync", "-av", "--delete"]
        # excludes 必须复用主 rsync 的同一份
        assert "--exclude=projects" in extra_cmd
        assert "--exclude=sessions" in extra_cmd
        # 源 = source_dir.parent / .claude；目标 = target_dir / claude；
        # 路径以 "/" 结尾以保留 rsync "拷贝内容" 语义。
        expected_src = f"{source_dir.parent / '.claude'}/"
        expected_tgt = f"{target_dir / 'claude'}/"
        assert extra_cmd[-2] == expected_src
        assert extra_cmd[-1] == expected_tgt
        # target 子目录应被自动创建
        assert (target_dir / "claude").is_dir()

    def test_raises_when_extra_source_missing(self, tmp_path: Path):
        # 配置了 extra sync 但源不存在 → 立即报错，避免主 rsync
        # 成功后才意外丢失配置（claude_code 的 .claude 是必备产物）。
        service = _make_service()
        service._run_local_command = MagicMock(return_value=None)

        source_dir, target_dir = _seed_dirs(tmp_path, with_extra_source=False)
        plan = _make_plan(extra_src=".claude", extra_tgt="claude")

        with pytest.raises(BotBuildMigrationError) as excinfo:
            service._migrate_bot_instance(
                device_id="dev1",
                source_dir=source_dir,
                target_dir=target_dir,
                version_str="v1",
                is_nas=True,
                nas_storage_id=str(source_dir),
                build_plan=plan,
                provider=MagicMock(),
            )

        assert "Extra sync source does not exist" in str(excinfo.value)
        # 主 rsync 必须先成功调用过一次（异常发生在 extra 之前的检查后）
        main_calls = [
            call
            for call in service._run_local_command.call_args_list
            if call.kwargs.get("command_name") == "rsync"
        ]
        assert len(main_calls) == 1
        # extra rsync 因为源缺失，不应被调用
        extra_calls = [
            call
            for call in service._run_local_command.call_args_list
            if call.kwargs.get("command_name") == "rsync (extra)"
        ]
        assert extra_calls == []


@pytest.mark.unit
def test_mcp_config_skipped_when_device_returns_non_json_stdout():
    """exec_shell_new 在 local 落基类返非 JSON 回显串→json.loads 失败软返 False，
    迁移链不阻塞、device 完全可注入可控（坐实唯一 🟠 为评估误判）。"""
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    service._device_service.exec_shell_new.return_value = MagicMock(
        stdout="Command executed on dev-1: cat mcporter.json", exit_code=0,
    )
    ok = service._generate_mcp_config(device_id="dev-1", target_dir="/tmp/x")
    assert ok is False
    service._device_service.exec_shell_new.assert_called()
