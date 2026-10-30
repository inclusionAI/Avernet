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
import json
from unittest.mock import MagicMock, call

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
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
        assert extra_cmd[:5] == [
            "sudo",
            "rsync",
            "-av",
            "--delete",
            "--delete-excluded",
        ]
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

    def test_skips_when_extra_source_missing(self, tmp_path: Path):
        # 配置了 extra sync 但源不存在 → 跳过该项，不影响主 rsync。
        service = _make_service()
        service._run_local_command = MagicMock(return_value=None)

        source_dir, target_dir = _seed_dirs(tmp_path, with_extra_source=False)
        plan = _make_plan(extra_src=".claude", extra_tgt="claude")

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
        # 主 rsync 仍应执行
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
def test_mcp_mcporter_content_is_written(tmp_path: Path):
    """Valid mcporter content is copied without a separate source lookup."""
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    service._device_service.exec_shell_new.return_value = MagicMock(
        stdout="[1, 2, 3]",
        exit_code=0,
    )
    target_dir = tmp_path / "target"
    ok = service._generate_mcp_config(device_id="dev-1", target_dir=target_dir)

    assert ok is True
    assert json.loads(
        (target_dir / "workspace" / "config" / "mcporter.json").read_text(
            encoding="utf-8",
        )
    ) == [1, 2, 3]
    service._device_service.exec_shell_new.assert_called_once_with(
        device_id="dev-1",
        shell_cmd="cat /home/admin/.mcporter/mcporter.json",
    )


@pytest.mark.unit
def test_mcp_fallback_reads_pretty_name_when_mcporter_is_missing(tmp_path: Path):
    """A missing read-only source file uses the container OS description."""
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()

    def fake_exec_shell_new(*, device_id: str, shell_cmd: str):
        results = {
            "cat /home/admin/.mcporter/mcporter.json": MagicMock(
                stdout="",
                stderr="cat: ...: No such file or directory",
                exit_code=1,
            ),
            "cat /etc/os-release": MagicMock(
                stdout='PRETTY_NAME="Arca Linux Test"\nID=arca',
                stderr="",
                exit_code=0,
            ),
        }
        return results[shell_cmd]

    service._device_service.exec_shell_new.side_effect = fake_exec_shell_new
    target_dir = tmp_path / "target"
    ok = service._generate_mcp_config(device_id="dev-1", target_dir=target_dir)

    assert ok is True
    assert json.loads(
        (target_dir / "workspace" / "config" / "mcporter.json").read_text(
            encoding="utf-8",
        )
    ) == {"context": "Arca Linux Test"}
    assert service._device_service.exec_shell_new.call_count == 2
    service._device_service.exec_shell_new.assert_has_calls([
        call(
            device_id="dev-1",
            shell_cmd="cat /home/admin/.mcporter/mcporter.json",
        ),
        call(device_id="dev-1", shell_cmd="cat /etc/os-release"),
    ])


@pytest.mark.unit
def test_mcp_fallback_is_safe_when_source_context_is_unavailable(tmp_path: Path):
    """The migration degrades to false instead of creating an empty config."""
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    service._device_service.exec_shell_new.side_effect = [
        MagicMock(stdout="", stderr="missing file", exit_code=1),
        MagicMock(stdout="ID=arca", stderr="", exit_code=0),
    ]
    target_dir = tmp_path / "target"
    ok = service._generate_mcp_config(device_id="dev-1", target_dir=target_dir)

    assert ok is False
    assert not list(target_dir.rglob("mcporter.json"))
