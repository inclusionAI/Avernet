"""Service Bot 版本化制品与 Published Runtime 的权限契约。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.service_bot.services import bot_build_service as module
from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildMigrationError,
    BotBuildService,
)
from agentclaw.community.core.workspace.engine_sandbox import EngineSandboxRegistry
from agentclaw.community.core.workspace.engines.claude_code import (
    ClaudeCodeSandboxProvider,
)
from agentclaw.community.core.workspace.engines.openclaw import (
    OpenClawSandboxProvider,
)
from agentclaw.community.di.config import WorkspaceConfig
from agentclaw.community.kernel.device_dto import CommandResult


def _make_service(*, registry: EngineSandboxRegistry) -> BotBuildService:
    whitelist_service = MagicMock()
    whitelist_service.is_bot_feature_enabled.return_value = False
    device_service = MagicMock()
    device_service.exec_shell_new.return_value = CommandResult(
        stdout='{"mcpServers": {}}',
        exit_code=0,
    )
    channel_service = MagicMock()
    channel_service.generate_openclaw_configs = AsyncMock(
        return_value=SimpleNamespace(verify="{}", online="{}")
    )
    return BotBuildService(
        device_service=device_service,
        baas_service=MagicMock(),
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=registry,
        channel_service=channel_service,
        bot_repository=MagicMock(),
        common_whitelist_service=whitelist_service,
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="",
    )


def _registry() -> EngineSandboxRegistry:
    workspace = WorkspaceConfig(
        openclaw_root="/home/admin/.openclaw",
        claude_code_root="/home/admin/.claude_code",
        claude_code_session_root="/home/admin/.claude",
    )
    registry = EngineSandboxRegistry()
    registry.register(OpenClawSandboxProvider(workspace=workspace))
    registry.register(ClaudeCodeSandboxProvider(workspace=workspace))
    return registry


@pytest.mark.unit
@pytest.mark.parametrize(
    ("engine", "expected_events"),
    [
        (
            "openclaw",
            ["migrate", "prepare", "mcp", "stage-configs", "finalize"],
        ),
        ("claude_code", ["migrate", "prepare", "mcp", "finalize"]),
    ],
)
def test_build_finalizes_complete_artifact_after_all_writers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    engine: str,
    expected_events: list[str],
) -> None:
    """build() 交付前必须在所有制品写入者之后统一收口。"""
    nas_root = tmp_path / "nas"
    artifact_root = tmp_path / "artifacts"
    nas_root.mkdir()
    monkeypatch.setattr(module, "get_bot_nas_dir", lambda **_: nas_root)
    monkeypatch.setattr(module, "get_bot_dir", lambda **_: artifact_root)

    service = _make_service(registry=_registry())
    events: list[str] = []

    def migrate(**kwargs: object) -> bool:
        events.append("migrate")
        Path(kwargs["target_dir"]).mkdir(parents=True)
        return True

    def generate_mcp(**kwargs: object) -> bool:
        events.append("mcp")
        target_dir = Path(kwargs["target_dir"])
        config = target_dir / "workspace" / "config" / "mcporter.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text("{}", encoding="utf-8")
        return True

    def generate_stage_configs(**kwargs: object) -> bool:
        events.append("stage-configs")
        target_dir = Path(kwargs["target_dir"])
        (target_dir / "openclaw_verify.json").write_text("{}", encoding="utf-8")
        return True

    def finalize(target_dir: Path) -> None:
        assert (target_dir / "workspace" / "config" / "mcporter.json").is_file()
        if engine == "openclaw":
            assert (target_dir / "openclaw_verify.json").is_file()
        events.append("finalize")

    def prepare(target_dir: Path) -> None:
        assert target_dir.is_dir()
        events.append("prepare")

    service._migrate_bot_instance = MagicMock(side_effect=migrate)
    service._prepare_artifact_for_build = MagicMock(side_effect=prepare)
    service._generate_mcp_config = MagicMock(side_effect=generate_mcp)
    service._generate_openclaw_stage_configs = MagicMock(
        side_effect=generate_stage_configs
    )
    service._finalize_runtime_artifact = MagicMock(side_effect=finalize)

    result = service.build(
        bot={
            "bot_id": "service-bot",
            "entity_id": "168944",
            "entity_type": "staff",
            "device_id": "device-1",
            "active_engine": engine,
        },
        version=1,
    )

    assert result["success"] is True
    assert events == expected_events


@pytest.mark.unit
def test_artifact_finalizer_is_symlink_safe_and_probes_as_runtime_admin(
    tmp_path: Path,
) -> None:
    """Finalizer 不得跟随 Pool 软链，且 Published Runtime 只能读制品。"""
    service = BotBuildService.__new__(BotBuildService)
    service._run_local_command = MagicMock()
    target_dir = tmp_path / "artifact" / "openclaw"
    target_dir.mkdir(parents=True)

    service._finalize_runtime_artifact(target_dir)

    calls = service._run_local_command.call_args_list
    assert [call.kwargs["command_name"] for call in calls] == [
        "seal artifact owner",
        "seal artifact mode",
        "verify artifact runtime readability",
    ]
    assert calls[0].kwargs["cmd"] == [
        "sudo",
        "chown",
        "-hR",
        "0:1000",
        str(target_dir),
    ]
    assert calls[1].kwargs["cmd"] == [
        "sudo",
        "chmod",
        "-R",
        "u=rwX,g=rX,o=",
        str(target_dir),
    ]
    probe_cmd = calls[2].kwargs["cmd"]
    assert probe_cmd[0] == "sudo"
    assert "-u" not in probe_cmd
    assert Path(probe_cmd[1]).name.startswith("python")
    assert probe_cmd[2:4] == ["-I", "-c"]
    assert "os.setgroups([])" in probe_cmd[4]
    assert "os.setgid(runtime_gid)" in probe_cmd[4]
    assert "os.setuid(runtime_uid)" in probe_cmd[4]
    assert "follow_symlinks=False" in probe_cmd[4]
    assert 'open(entry.path, "rb")' in probe_cmd[4]
    assert "artifact unexpectedly writable by published runtime" in probe_cmd[4]
    assert probe_cmd[5:] == ["1000", "1000", str(target_dir)]


@pytest.mark.unit
def test_artifact_prepare_restores_backend_writer_access_for_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """重试已封存版本前，应先用纯数字宿主身份恢复 Backend 写权限。"""
    monkeypatch.setattr(module.os, "geteuid", lambda: 43210)
    monkeypatch.setattr(module.os, "getegid", lambda: 43211)
    service = BotBuildService.__new__(BotBuildService)
    service._run_local_command = MagicMock()
    target_dir = tmp_path / "artifact"

    service._prepare_artifact_for_build(target_dir)

    calls = service._run_local_command.call_args_list
    assert [call.kwargs["cmd"] for call in calls] == [
        ["sudo", "mkdir", "-p", str(target_dir)],
        ["sudo", "chown", "-hR", "43210:43211", str(target_dir)],
        ["sudo", "chmod", "-R", "u+rwX", str(target_dir)],
    ]


@pytest.mark.unit
def test_artifact_finalizer_fails_closed_when_runtime_admin_cannot_read(
    tmp_path: Path,
) -> None:
    """admin 等价读取失败必须阻断 build，不能延迟到 BaaS hook。"""
    service = BotBuildService.__new__(BotBuildService)

    def run_command(**kwargs: object) -> None:
        if kwargs["command_name"] == "verify artifact runtime readability":
            raise BotBuildMigrationError(
                "artifact is not readable by published runtime: Operation not permitted"
            )

    service._run_local_command = MagicMock(side_effect=run_command)
    target_dir = tmp_path / "artifact"
    target_dir.mkdir()

    with pytest.raises(
        BotBuildMigrationError,
        match="artifact is not readable by published runtime",
    ):
        service._finalize_runtime_artifact(target_dir)


@pytest.mark.unit
def test_runtime_readability_probe_opens_files_without_following_pool_links(
    tmp_path: Path,
) -> None:
    """探测应验读普通文件，但绝不能进入 active Skill 的外部目标。"""
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "settings.json").write_text("{}", encoding="utf-8")
    (artifact / "active-skill").symlink_to(
        "/home/admin/.openclaw/workspace/skills-pool/skills-repo/active-skill",
        target_is_directory=True,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            module._RUNTIME_ARTIFACT_READABILITY_PROBE,
            str(artifact),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.unit
@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses Unix read permission bits")
def test_runtime_readability_probe_detects_unreadable_regular_file(
    tmp_path: Path,
) -> None:
    """本地回放 Published admin 遇到不可读制品时的 PermissionError。"""
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    unreadable = artifact / "settings.json"
    unreadable.write_text("{}", encoding="utf-8")
    unreadable.chmod(0)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                module._RUNTIME_ARTIFACT_READABILITY_PROBE,
                str(artifact),
            ],
            capture_output=True,
            text=True,
        )
    finally:
        unreadable.chmod(0o600)

    assert result.returncode != 0
    assert "PermissionError" in result.stderr
