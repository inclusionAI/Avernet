"""Service Bot 构建冻结 Skills 文件制品的行为测试。"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildService,
)
from agentclaw.community.core.workspace.engine_sandbox import (
    EngineSandboxRegistry,
)
from agentclaw.community.core.workspace.engines.claude_code import (
    ClaudeCodeSandboxProvider,
)
from agentclaw.community.core.workspace.engines.openclaw import (
    OpenClawSandboxProvider,
)
from agentclaw.community.di.config import WorkspaceConfig
from agentclaw.community.kernel.device_dto import CommandResult


def _make_service(
    device_service: MagicMock,
    *,
    registry: EngineSandboxRegistry,
    channel_service: MagicMock | None = None,
) -> BotBuildService:
    whitelist_service = MagicMock()
    whitelist_service.is_bot_feature_enabled.return_value = False
    return BotBuildService(
        device_service=device_service,
        baas_service=MagicMock(),
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=registry,
        channel_service=channel_service or MagicMock(),
        bot_repository=MagicMock(),
        common_whitelist_service=whitelist_service,
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="",
    )


def _install_passthrough_sudo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run build commands unprivileged inside the test's temporary directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo = bin_dir / "sudo"
    sudo.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    sudo.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _arrange_real_build_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    import agentclaw.community.core.service_bot.services.bot_build_service as build_module

    nas_root = tmp_path / "nas"
    artifact_root = tmp_path / "artifacts"
    nas_root.mkdir()
    monkeypatch.setattr(build_module, "get_bot_nas_dir", lambda **_: nas_root)
    monkeypatch.setattr(build_module, "get_bot_dir", lambda **_: artifact_root)
    _install_passthrough_sudo(monkeypatch, tmp_path)
    return nas_root, artifact_root


def _write_skill(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("test skill\n", encoding="utf-8")


@pytest.mark.unit
@pytest.mark.parametrize("engine", ["openclaw", "claude_code"])
def test_pool_build_uses_the_versioned_filesystem_snapshot_when_runtime_cannot_write_nfs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    engine: str,
):
    """A complete host-built artifact must not depend on a second runtime write."""
    nas_root, artifact_root = _arrange_real_build_paths(monkeypatch, tmp_path)
    workspace = WorkspaceConfig(
        openclaw_root="/home/admin/.openclaw",
        claude_code_root="/home/admin/.claude_code",
        claude_code_session_root="/home/admin/.claude",
    )
    registry = EngineSandboxRegistry()
    registry.register(OpenClawSandboxProvider(workspace=workspace))
    registry.register(ClaudeCodeSandboxProvider(workspace=workspace))

    if engine == "openclaw":
        engine_root = nas_root / ".openclaw"
        active_root = engine_root / "workspace" / "skills"
        pool_root = engine_root / "workspace" / "skills-pool"
        artifact_engine_root = artifact_root / "7" / "openclaw"
        artifact_active_root = artifact_engine_root / "workspace" / "skills"
        artifact_pool_root = artifact_engine_root / "workspace" / "skills-pool"
        runtime_pool_root = Path("/home/admin/.openclaw/workspace/skills-pool")
    else:
        engine_root = nas_root / ".claude_code"
        active_root = nas_root / ".claude" / "skills"
        pool_root = engine_root / "workspace" / "skills-pool"
        artifact_engine_root = artifact_root / "7" / "claude_code"
        artifact_active_root = artifact_engine_root / "claude" / "skills"
        artifact_pool_root = artifact_engine_root / "workspace" / "skills-pool"
        runtime_pool_root = Path("/home/admin/.claude_code/workspace/skills-pool")

    repo_target = pool_root / "skills-repo" / "repo-skill"
    local_target = pool_root / "skills-local" / "local-skill"
    _write_skill(repo_target)
    _write_skill(local_target)
    active_root.mkdir(parents=True)
    runtime_repo_target = runtime_pool_root / "skills-repo" / "repo-skill"
    runtime_local_target = runtime_pool_root / "skills-local" / "local-skill"
    (active_root / "repo-skill").symlink_to(
        runtime_repo_target,
        target_is_directory=True,
    )
    (active_root / "local-skill").symlink_to(
        runtime_local_target,
        target_is_directory=True,
    )

    device_service = MagicMock()

    def _exec_shell_new(*, shell_cmd: str, **_: object) -> CommandResult:
        if shell_cmd.startswith("rsync "):
            return CommandResult(
                stderr="Operation not permitted",
                exit_code=23,
                status="error",
            )
        return CommandResult(stdout='{"mcpServers": {}}', exit_code=0)

    device_service.exec_shell_new.side_effect = _exec_shell_new
    channel_service = MagicMock()
    channel_service.generate_openclaw_configs = AsyncMock(
        return_value=SimpleNamespace(verify="{}", online="{}")
    )
    service = _make_service(
        device_service,
        registry=registry,
        channel_service=channel_service,
    )

    result = service.build(
        bot={
            "bot_id": "pool-bot",
            "entity_id": "owner-1",
            "entity_type": "staff",
            "device_id": "device-1",
            "active_engine": engine,
        },
        version=7,
    )

    assert result["success"] is True
    assert (artifact_active_root / "repo-skill").is_symlink()
    assert (artifact_active_root / "local-skill").is_symlink()
    assert (artifact_active_root / "repo-skill").readlink() == runtime_repo_target
    assert (artifact_active_root / "local-skill").readlink() == runtime_local_target
    assert (artifact_pool_root / "skills-local" / "local-skill" / "SKILL.md").is_file()
    assert not (artifact_pool_root / "skills-repo").exists()
    assert all(
        not call.kwargs["shell_cmd"].startswith("rsync ")
        for call in device_service.exec_shell_new.call_args_list
    )
