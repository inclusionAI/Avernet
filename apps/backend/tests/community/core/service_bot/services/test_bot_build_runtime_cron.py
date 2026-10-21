"""Published artifacts omit runtime tasks; draft restore preserves local tasks."""
from pathlib import Path
import subprocess
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services import bot_build_service as module
from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService
from agentclaw.community.core.workspace.engines.aicoding import _AICODING_BUILD_PLAN
from agentclaw.community.core.workspace.engines.claude_code import _CLAUDE_CODE_BUILD_PLAN
from agentclaw.community.core.workspace.engines.openclaw import _OPENCLAW_BUILD_PLAN


PLANS = [_OPENCLAW_BUILD_PLAN, _CLAUDE_CODE_BUILD_PLAN, _AICODING_BUILD_PLAN]


def write(root: Path, name: str, value: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)


def run_without_privilege(*, cmd, **kwargs):
    # The actual service command runs against temporary dirs, without sudo/chown.
    args = cmd[1:] if cmd[0] == "sudo" else cmd
    args = [arg for arg in args if not arg.startswith("--chown=")]
    subprocess.run(args, check=True, capture_output=True)


@pytest.mark.parametrize("plan", PLANS, ids=lambda plan: plan.engine_type)
def test_publish_retry_removes_old_cron_and_never_imports_source_tasks(tmp_path, plan):
    source, target = tmp_path / "source", tmp_path / "artifact"
    for name in ("cron/jobs.json", "cron/jobs.json.bak", "cron/runs/run.jsonl",
                 "sessions/cron-tasks.json", "sessions/cron-runs.json"):
        write(source, name, "source-task")
        write(target, name, "old-artifact-task")
    write(source, "workspace/app.py", "published-code")
    write(source, "workspace/cron/example.txt", "unrelated-workspace-file")
    if plan.engine_type == "aicoding":
        write(source.parent, ".codefuse/fuse/codefuse.json", "published-codefuse-config")
    if plan.extra_sync_source_relpath:
        (source.parent / plan.extra_sync_source_relpath).mkdir()
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    service._run_local_command = run_without_privilege

    assert service._migrate_bot_instance(
        device_id="device-a", source_dir=source, target_dir=target,
        version_str="1", is_nas=True, nas_storage_id=source,
        build_plan=plan,
    )
    assert not (target / "cron").exists()
    if plan.engine_type != "openclaw":
        assert not (target / "sessions/cron-tasks.json").exists()
        assert not (target / "sessions/cron-runs.json").exists()
    assert (target / "workspace/app.py").read_text() == "published-code"
    assert (target / "workspace/cron/example.txt").exists()
    assert (source / "cron/jobs.json").read_text() == "source-task"
    if plan.engine_type == "aicoding":
        assert (target / "home/.codefuse/fuse/codefuse.json").read_text() == "published-codefuse-config"
    service._device_service.exec_shell_new.assert_not_called()


@pytest.mark.parametrize("plan", PLANS, ids=lambda plan: plan.engine_type)
def test_restore_legacy_artifact_preserves_draft_runtime_tasks(tmp_path, monkeypatch, plan):
    bot_root, nas_root = tmp_path / "bot-data", tmp_path / "nas"
    artifact = bot_root / "3" / plan.migration_subpath
    draft = nas_root / plan.source_root_name
    for name in ("cron/jobs.json", "cron/jobs.json.bak", "cron/runs/run.jsonl",
                 "sessions/cron-tasks.json", "sessions/cron-runs.json"):
        write(artifact, name, "legacy-published-task")
        write(draft, name, "draft-only-task")
    write(artifact, "workspace/app.py", "restored-code")
    if plan.engine_type == "aicoding":
        write(artifact, "home/.codefuse/fuse/codefuse.json", "restored-codefuse-config")
    if plan.extra_sync_target_relpath:
        (artifact / plan.extra_sync_target_relpath).mkdir()
    provider = MagicMock()
    provider.get_build_plan.return_value = plan
    registry = MagicMock()
    registry.resolve.return_value = provider
    baas = MagicMock()
    baas.resolve_container_provider.return_value = "baas"
    service = BotBuildService(
        user_list_repository=MagicMock(exists=MagicMock(return_value=False)),
        artifact_transfer=MagicMock(try_publish=MagicMock(return_value=False),
                                    try_restore=MagicMock(return_value=False)),
        build_ignore_repository=MagicMock(), env="test",
        device_service=MagicMock(), baas_service=baas,
        path_factory=MagicMock(), passport_plugin=MagicMock(),
        device_binding_repo=MagicMock(), sandbox_registry=registry,
        channel_service=MagicMock(), bot_repository=MagicMock(),
        common_whitelist_service=MagicMock(), baas_template_resolver=MagicMock(),
        teclaw_template_uuid="teclaw-template",
    )
    service._run_local_command = run_without_privilege
    monkeypatch.setattr(module, "get_bot_dir", lambda *args, **kwargs: bot_root)
    monkeypatch.setattr(module, "get_bot_nas_dir", lambda *args, **kwargs: nas_root)

    result = service.restore_draft(
        bot={"bot_id": "b1", "entity_id": "owner", "entity_type": "staff",
             "active_engine": plan.engine_type, "device_id": "draft-device"},
        source_version=3,
        artifact_ext={"migration_path": str(artifact)},
    )
    assert result["restore_type"] == "migration_path"
    assert (draft / "cron/jobs.json").read_text() == "draft-only-task"
    if plan.engine_type != "openclaw":
        assert (draft / "sessions/cron-tasks.json").read_text() == "draft-only-task"
        assert (draft / "sessions/cron-runs.json").read_text() == "draft-only-task"
    assert (draft / "workspace/app.py").read_text() == "restored-code"
    if plan.engine_type == "aicoding":
        assert (nas_root / ".codefuse/fuse/codefuse.json").read_text() == "restored-codefuse-config"
