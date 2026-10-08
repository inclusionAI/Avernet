"""Build rules must exclude actual bytes, including secondary copy sources."""

from dataclasses import replace
from pathlib import Path
import subprocess
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan
from agentclaw.community.kernel.build_ignore import BuildIgnoreConfig


def plan(**kwargs):
    return replace(EngineBuildPlan(
        engine_type="claude_code", source_root_name=".claude_code",
        migration_subpath="claude_code", workspace_subdir="workspace",
        mcp_config_relpath="workspace/config/mcporter.json",
        skill_source_relpath="workspace/skills", skill_target_relpath="workspace/skills",
        rsync_excludes=[], extra_include_files=["sessions/cron.json"],
        extra_sync_source_relpath=".claude", extra_sync_target_relpath="claude",
    ), **kwargs)


def write(root, relative, content="payload"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


@pytest.mark.unit
@pytest.mark.parametrize("rules", [
    ("workspace/bin", "sessions/cron.json", "claude/skills/drop"),
    ("workspace/bin", "sessions/cron.json", "claude"),
])
def test_real_rsync_excludes_all_copy_routes_and_stale_retry_items(tmp_path, rules):
    source, target = tmp_path / "source", tmp_path / "target"
    extra = tmp_path / ".claude"
    for path in ["workspace/bin/deep/file", "workspace/binary/file", "x/workspace/bin/file", "sessions/cron.json"]:
        write(source, path)
    for path in ["skills/drop/file", "skills/keep/file", "workspace/bin/file"]:
        write(extra, path)
    write(target, "workspace/bin/stale")
    write(target, "claude/skills/drop/stale")
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()

    def run(*, cmd, **kwargs):
        subprocess.run(cmd[1:] if cmd[0] == "sudo" else cmd, check=True, capture_output=True)

    service._run_local_command = run
    assert service._migrate_bot_instance(
        device_id="d", source_dir=source, target_dir=target, version_str="1",
        is_nas=True, nas_storage_id=tmp_path, build_plan=plan(),
        build_ignore_paths=rules,
    )
    assert not (target / "workspace/bin").exists()
    assert not (target / "sessions/cron.json").exists()
    assert (target / "workspace/binary/file").read_text() == "payload"
    assert (target / "x/workspace/bin/file").exists()
    assert not (target / "claude/skills/drop").exists()
    if "claude" in rules:
        assert not (target / "claude").exists()
    else:
        assert (target / "claude/skills/keep/file").exists()
        assert (target / "claude/workspace/bin/file").exists()
    assert (source / "workspace/bin/deep/file").exists()


@pytest.mark.unit
def test_excluded_extra_include_does_not_probe_or_fallback(tmp_path, monkeypatch):
    service = BotBuildService.__new__(BotBuildService)
    service._device_service = MagicMock()
    def forbidden(*args, **kwargs):
        pytest.fail("excluded source must not be probed")
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "exists", forbidden)
        service._sync_extra_include_files(
            source_dir=tmp_path, target_dir=tmp_path, build_plan=plan(),
            command_name="extra", error_message="failed", device_id="d",
            device_source_root="/runtime", build_ignore_paths=("sessions",),
        )


def build_service(tmp_path, monkeypatch, record):
    import agentclaw.community.core.service_bot.services.bot_build_service as module
    service = BotBuildService.__new__(BotBuildService)
    service._env = "test"
    service._build_ignore_repository = MagicMock(get=MagicMock(return_value=record))
    service._resolve_sandbox_provider = MagicMock(return_value=MagicMock(
        get_build_plan=MagicMock(return_value=plan()),
    ))
    service._get_migration_path_base = lambda **_: "/artifact"
    service._migrate_bot_instance = MagicMock(return_value=True)
    monkeypatch.setattr(module, "get_bot_nas_dir", lambda **_: tmp_path)
    monkeypatch.setattr(module, "get_bot_dir", lambda **_: tmp_path)
    return service


@pytest.mark.unit
@pytest.mark.parametrize("record,want", [
    (None, {"engine_type": "claude_code", "paths": [], "revision": 0}),
    (BuildIgnoreConfig(("workspace/bin",), 4),
     {"engine_type": "claude_code", "paths": ["workspace/bin"], "revision": 4}),
])
def test_build_captures_one_scoped_snapshot(tmp_path, monkeypatch, record, want):
    service = build_service(tmp_path, monkeypatch, record)
    result = service.build({"bot_id": "b", "entity_id": "e"}, version=3)
    assert result["publish_ignore"] == want
    service._build_ignore_repository.get.assert_called_once_with(
        env="test", entity_id="e", bot_id="b", engine_type="claude_code",
    )
    assert service._migrate_bot_instance.call_args.kwargs["build_ignore_paths"] == tuple(want["paths"])


@pytest.mark.unit
def test_snapshot_does_not_change_during_build(tmp_path, monkeypatch):
    record = SimpleNamespace(paths=["workspace/bin"], revision=4)
    service = build_service(tmp_path, monkeypatch, record)
    def migrate(**kwargs):
        record.paths.append("workspace/other")
        record.revision = 5
        return True
    service._migrate_bot_instance = migrate
    result = service.build({"bot_id": "b", "entity_id": "e"})
    assert result["publish_ignore"]["paths"] == ["workspace/bin"]
    assert result["publish_ignore"]["revision"] == 4


@pytest.mark.unit
@pytest.mark.parametrize("failed", [False, True])
def test_transfer_logs_snapshot_and_result_without_exception_body(tmp_path, monkeypatch, failed):
    import agentclaw.community.core.service_bot.services.bot_build_service as module
    from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildMigrationError
    service = build_service(tmp_path, monkeypatch, BuildIgnoreConfig(("workspace/bin",), 4))
    events = []
    logger = MagicMock()
    logger.info.side_effect = lambda message, *args: events.append(message % args if args else message)
    monkeypatch.setattr(module, "logger", logger)
    if failed:
        service._migrate_bot_instance.side_effect = BotBuildMigrationError("secret-do-not-log")
        with pytest.raises(BotBuildMigrationError):
            service.build({"bot_id": "b", "entity_id": "e"}, version=3)
    else:
        service.build({"bot_id": "b", "entity_id": "e"}, version=3)
    transfer = next(event for event in events if event.startswith("build_ignore.transfer"))
    assert f"status={'failure' if failed else 'success'}" in transfer
    assert "revision=4" in transfer and "version=3" in transfer
    assert "secret-do-not-log" not in transfer


@pytest.mark.unit
def test_database_failure_aborts_without_copy_or_sensitive_logs(tmp_path, monkeypatch, caplog):
    service = build_service(tmp_path, monkeypatch, None)
    service._build_ignore_repository.get.side_effect = RuntimeError("secret-do-not-log")
    with pytest.raises(Exception, match="build_ignore_snapshot_failed"):
        service.build({"bot_id": "b", "entity_id": "e"})
    service._migrate_bot_instance.assert_not_called()
    assert "secret-do-not-log" not in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize("rule", ["workspace", "workspace/config", "workspace/config/mcporter.json"])
def test_required_mcp_path_blocks_build_before_copy(tmp_path, monkeypatch, rule):
    service = build_service(tmp_path, monkeypatch, BuildIgnoreConfig((rule,), 1))
    with pytest.raises(ValueError, match="required_build_path:workspace/config/mcporter.json"):
        service.build({"bot_id": "b", "entity_id": "e"})
    service._migrate_bot_instance.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("name", ["openclaw.json", "openclaw_verify.json", "openclaw_online.json", "openclaw_eval.json"])
def test_openclaw_required_files_rejected(name):
    from agentclaw.community.core.service_bot.services.build_ignore_rules import validate_required_paths
    with pytest.raises(ValueError, match="required_build_path:" + name):
        validate_required_paths((name,), plan(engine_type="openclaw"))


@pytest.mark.unit
@pytest.mark.parametrize("success", [True, False])
def test_producer_pins_only_successful_snapshot(success):
    from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import ArcaSnapshotProducer
    from agentclaw.community.core.service_bot.services.deploy.artifact_build_request import ArtifactBuildRequest
    snapshot = {"engine_type": "claude_code", "paths": ["workspace/bin"], "revision": 4}
    builder = MagicMock(capture=MagicMock(return_value=None))
    service = MagicMock(build=MagicMock(return_value={"success": success, "publish_ignore": snapshot}))
    artifact = ArcaSnapshotProducer(service, builder).produce_artifact(
        ArtifactBuildRequest.create(bot={"bot_id": "b"}, version=1),
    )
    assert artifact.success is success
    assert artifact.ext.get("publish_ignore") == (snapshot if success else None)
