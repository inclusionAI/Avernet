"""Bot Build 对 active Skills mapping 快照失败的行为测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildMigrationError,
    BotBuildService,
)
from agentclaw.community.core.workspace.engine_sandbox import (
    EngineBuildPlan,
    EngineSandboxRegistry,
)
from agentclaw.community.kernel.device_dto import CommandResult


class _TestSandboxProvider:
    @property
    def engine_type(self) -> str:
        return "test-engine"

    def get_base_path(self) -> str:
        return "/home/admin/.test-engine"

    def get_build_plan(
        self,
        build_rsync_excludes_append: list[str] | None = None,
    ) -> EngineBuildPlan:
        return EngineBuildPlan(
            engine_type=self.engine_type,
            source_root_name=".test-engine",
            migration_subpath="test-engine",
            workspace_subdir="workspace",
            mcp_config_relpath="workspace/config/mcporter.json",
            skill_source_relpath="workspace/skills",
            skill_target_relpath="workspace/skills",
            rsync_excludes=[],
        )


def _make_service(
    device_service: MagicMock,
    *,
    mount_home_dir_storage: bool = False,
) -> BotBuildService:
    registry = EngineSandboxRegistry()
    registry.register(_TestSandboxProvider())
    whitelist_service = MagicMock()
    whitelist_service.is_bot_feature_enabled.return_value = mount_home_dir_storage
    return BotBuildService(
        device_service=device_service,
        baas_service=MagicMock(),
        path_factory=MagicMock(),
        passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(),
        sandbox_registry=registry,
        channel_service=MagicMock(),
        bot_repository=MagicMock(),
        common_whitelist_service=whitelist_service,
        baas_template_resolver=MagicMock(),
        teclaw_template_uuid="",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mount_home_dir_storage", "expected_root"),
    [
        (True, "/opt/nfs/bot-data"),
        (False, "/home/admin/nfs/bot-data"),
    ],
)
def test_build_snapshots_active_skills_into_the_resolved_mount(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mount_home_dir_storage: bool,
    expected_root: str,
):
    """The snapshot and deploy pointer share the selected container mount."""
    _arrange_build_paths(monkeypatch, tmp_path)
    device_service = MagicMock()
    device_service.exec_shell_new.side_effect = [
        CommandResult(exit_code=0),
        CommandResult(stdout='{"mcpServers": {}}', exit_code=0),
    ]
    service = _make_service(
        device_service,
        mount_home_dir_storage=mount_home_dir_storage,
    )

    result = service.build(
        bot={
            "bot_id": "bot-1",
            "entity_id": "owner-1",
            "entity_type": "staff",
            "device_id": "device-1",
            "active_engine": "test-engine",
        },
        version=7,
    )

    snapshot_command = device_service.exec_shell_new.call_args_list[0].kwargs[
        "shell_cmd"
    ]
    assert result["migration_path"] == f"{expected_root}/7/test-engine"
    assert snapshot_command.startswith(
        "rsync -av --delete --delete-excluded "
        "--exclude='skills-repo' --exclude='.skills-repo*' "
    )
    assert snapshot_command.endswith(
        "/home/admin/.test-engine/workspace/skills/ "
        f"{expected_root}/7/test-engine/workspace/skills/"
    )


def _arrange_build_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import agentclaw.community.core.service_bot.services.bot_build_service as build_module

    nas_root = tmp_path / "nas"
    (nas_root / ".test-engine").mkdir(parents=True)
    monkeypatch.setattr(build_module, "get_bot_nas_dir", lambda **_: nas_root)
    monkeypatch.setattr(
        build_module,
        "get_bot_dir",
        lambda **_: tmp_path / "artifacts",
    )
    monkeypatch.setattr(
        build_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )


@pytest.mark.unit
def test_build_fails_when_active_skill_mapping_snapshot_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _arrange_build_paths(monkeypatch, tmp_path)
    device_service = MagicMock()
    device_service.exec_shell_new.side_effect = [
        RuntimeError("sandbox unavailable"),
        CommandResult(stdout='{"mcpServers": {}}', exit_code=0),
    ]
    service = _make_service(device_service)

    with pytest.raises(
        BotBuildMigrationError,
        match="active Skills mapping snapshot failed",
    ):
        service.build(
            bot={
                "bot_id": "bot-1",
                "entity_id": "owner-1",
                "entity_type": "staff",
                "device_id": "device-1",
                "active_engine": "test-engine",
            },
            version=7,
            active_skills_snapshot_required=True,
        )


@pytest.mark.unit
def test_build_fails_when_active_skill_mapping_snapshot_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _arrange_build_paths(monkeypatch, tmp_path)
    device_service = MagicMock()
    device_service.exec_shell_new.side_effect = [
        CommandResult(stderr="rsync failed", exit_code=23, status="error"),
        CommandResult(stdout='{"mcpServers": {}}', exit_code=0),
    ]
    service = _make_service(device_service)

    with pytest.raises(
        BotBuildMigrationError,
        match="active Skills mapping snapshot failed.*exit_code=23",
    ):
        service.build(
            bot={
                "bot_id": "bot-1",
                "entity_id": "owner-1",
                "entity_type": "staff",
                "device_id": "device-1",
                "active_engine": "test-engine",
            },
            version=7,
            active_skills_snapshot_required=True,
        )


@pytest.mark.unit
def test_legacy_build_keeps_snapshot_failure_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Legacy publication keeps the pre-Pool failure semantics."""
    _arrange_build_paths(monkeypatch, tmp_path)
    device_service = MagicMock()
    device_service.exec_shell_new.side_effect = [
        CommandResult(stderr="rsync failed", exit_code=23, status="error"),
        CommandResult(stdout='{"mcpServers": {}}', exit_code=0),
    ]
    service = _make_service(device_service)

    result = service.build(
        bot={
            "bot_id": "legacy-bot",
            "entity_id": "owner-1",
            "entity_type": "staff",
            "device_id": "device-1",
            "active_engine": "test-engine",
        },
        version=7,
        active_skills_snapshot_required=False,
    )

    assert result["success"] is True
    assert device_service.exec_shell_new.call_count == 2
