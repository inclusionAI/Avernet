"""CLI snapshots copy independently of the engine root and retry cleanly."""

import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildMigrationError,
    BotBuildService,
)
from agentclaw.community.core.service_bot.services import bot_build_service as module
from agentclaw.community.core.workspace.engine_sandbox import EngineBuildPlan
from agentclaw.community.core.service_bot.services.local_bin_artifact import (
    HOME_ADMIN_ARTIFACT_ROOT,
    restore_local_bin_artifact,
    sync_local_bin_artifact,
)


@pytest.fixture
def migration(tmp_path):
    source = tmp_path / "home/.openclaw"
    source.mkdir(parents=True)
    (source / "config.json").write_text("{}")
    target = tmp_path / "artifacts/3/openclaw"
    service = BotBuildService.__new__(BotBuildService)
    service._sync_extra_include_files = MagicMock()
    commands = []

    def run_command(**kwargs):
        cmd = kwargs["cmd"]
        commands.append(cmd)
        if cmd[1] == "chmod":
            return
        result = subprocess.run(cmd[1:], capture_output=True, text=True)
        if result.returncode:
            raise BotBuildMigrationError(result.stderr)

    service._run_local_command = run_command

    def migrate(rules=()):
        return service._migrate_bot_instance(
            device_id="device-cli-test",
            source_dir=source,
            target_dir=target,
            version_str="2",
            is_nas=True,
            nas_storage_id=source.parent,
            build_ignore_paths=rules,
        )

    return source, target, commands, migrate


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_migration_copies_only_bin_and_materializes_executable_link(migration, caplog):
    caplog.set_level("INFO")
    source, target, commands, migrate = migration
    local = source.parent / ".local"
    (local / "bin").mkdir(parents=True)
    tools = local / "share/tools"
    tools.mkdir(parents=True)
    tool = tools / "cli"
    tool.write_text("#!/bin/sh\nprintf 'cli works'\n")
    tool.chmod(0o755)
    (local / "bin/cli").symlink_to("../share/tools/cli")
    (local / "credentials").write_text("private-source-content")

    assert migrate()

    cli = target / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin/cli"
    assert cli.is_file() and not cli.is_symlink()
    assert cli.stat().st_mode & 0o111 == 0o111
    assert subprocess.check_output([str(cli)], text=True) == "cli works"
    assert not (target / HOME_ADMIN_ARTIFACT_ROOT / ".local/share").exists()
    assert not (target / HOME_ADMIN_ARTIFACT_ROOT / ".local/credentials").exists()
    assert (target / "config.json").read_text() == "{}"
    assert any("-avL" in cmd for cmd in commands)
    for event in ("request", "success"):
        assert f"service_bot.local_bin.{event}" in caplog.text
    assert "device_id=device-cli-test" in caplog.text
    assert "version=2" in caplog.text
    assert "source=" in caplog.text and "target=" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "private-source-content" not in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("rule", [".local", ".local/bin"])
def test_migration_directory_ignore_skips_cli_and_clears_previous_artifact(migration, rule):
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "cli").write_text("published")
    assert migrate()
    assert (target / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin/cli").exists()

    assert migrate((rule,))
    assert not (target / HOME_ADMIN_ARTIFACT_ROOT / ".local").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_migration_child_ignore_omits_only_selected_cli(migration):
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    (source_bin / "private").mkdir(parents=True)
    (source_bin / "private/cli").write_text("skip")
    (source_bin / "cli").write_text("keep")
    (source_bin / "skip-cli").write_text("skip")
    assert migrate((".local/bin/private", ".local/bin/skip-cli"))
    assert (target / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin/cli").read_text() == "keep"
    assert not (target / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin/private").exists()
    assert not (target / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin/skip-cli").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_missing_home_bin_never_republishes_stale_engine_snapshot(migration, caplog):
    caplog.set_level("INFO")
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "cli").write_text("old")
    assert migrate()
    shutil.rmtree(source.parent / ".local")
    # The engine root might contain a snapshot from an older restore. It is
    # not the draft home's source and must never be reused.
    engine_bin = source / ".local/bin"
    engine_bin.mkdir(parents=True)
    (engine_bin / "cli").write_text("stale")
    assert migrate()
    assert not (target / HOME_ADMIN_ARTIFACT_ROOT / ".local").exists()
    assert (target / ".local/bin/cli").read_text() == "stale"
    assert "service_bot.local_bin.skip" in caplog.text
    assert "reason=source_missing" in caplog.text


def test_cli_copy_failure_propagates_with_context(tmp_path, caplog):
    caplog.set_level("INFO")
    source = tmp_path / "home"
    (source / ".local/bin").mkdir(parents=True)
    run = MagicMock(side_effect=BotBuildMigrationError("copy failed"))
    with pytest.raises(BotBuildMigrationError, match="copy failed"):
        sync_local_bin_artifact(
            source_home=source,
            target_dir=tmp_path / "artifact",
            device_id="device-cli-test",
            version="2",
            build_ignore_paths=(),
            run_command=run,
        )
    assert "service_bot.local_bin.request" in caplog.text
    assert "service_bot.local_bin.failure" in caplog.text
    assert "error_type=BotBuildMigrationError" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "service_bot.local_bin.success" not in caplog.text


def _restore_service(monkeypatch, artifact, target_home):
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
    service = BotBuildService.__new__(BotBuildService)
    service._resolve_sandbox_provider = MagicMock(
        return_value=MagicMock(get_build_plan=MagicMock(return_value=plan)),
    )
    service._sync_extra_include_files = MagicMock()
    commands = []

    def run_command(**kwargs):
        cmd = kwargs["cmd"]
        commands.append(cmd)
        if cmd[1] == "chmod":
            return
        # The host test is unprivileged; ownership mapping is asserted below
        # while the same rsync argv performs real archive/merge operations.
        result = subprocess.run(
            [arg for arg in cmd[1:] if not arg.startswith("--chown=")],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise BotBuildMigrationError(result.stderr)

    service._run_local_command = run_command
    monkeypatch.setattr(module, "get_bot_dir", lambda *args, **kwargs: artifact.parent.parent)
    monkeypatch.setattr(module, "get_bot_nas_dir", lambda *args, **kwargs: target_home)

    def restore():
        return service.restore_draft(
            bot={"bot_id": "restore-cli", "entity_id": "owner"},
            source_version=3,
            artifact_ext={"migration_path": str(artifact)},
        )

    return commands, restore


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_build_and_restore_keep_engine_and_home_cli_separate(migration, monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    source, artifact, _, migrate = migration
    engine_bin = source / ".local/bin"
    home_bin = source.parent / ".local/bin"
    engine_bin.mkdir(parents=True)
    home_bin.mkdir(parents=True)
    (engine_bin / "cli").write_text("engine-cli")
    (source / ".local/engine-data").write_text("engine state")
    (home_bin / "cli").write_text("#!/bin/sh\nprintf 'home-cli'\n")
    (home_bin / "cli").chmod(0o755)
    # An engine cannot supply the reserved Home metadata root.
    (source / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin").mkdir(parents=True)
    (source / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin/forged").write_text("skip")
    assert migrate()
    assert (artifact / ".local/bin/cli").read_text() == "engine-cli"
    assert (artifact / ".local/engine-data").read_text() == "engine state"
    assert not (artifact / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin/forged").exists()

    restored_home = tmp_path / "restored-home"
    (restored_home / ".local/bin").mkdir(parents=True)
    (restored_home / ".local/bin/cli").write_text("previous home CLI")
    (restored_home / ".local/bin/target-only").write_text("keep target CLI")
    (restored_home / ".local/share").mkdir()
    (restored_home / ".local/share/state").write_text("keep home state")
    # Only bin can leave the metadata root, even if another directory exists.
    (artifact / HOME_ADMIN_ARTIFACT_ROOT / ".local/share").mkdir()
    (artifact / HOME_ADMIN_ARTIFACT_ROOT / ".local/share/state").write_text("do not restore")
    commands, restore = _restore_service(monkeypatch, artifact, restored_home)
    assert restore()["restore_type"] == "migration_path"
    assert (restored_home / ".openclaw/.local/bin/cli").read_text() == "engine-cli"
    assert (restored_home / ".openclaw/.local/engine-data").read_text() == "engine state"
    assert not (restored_home / ".openclaw" / HOME_ADMIN_ARTIFACT_ROOT).exists()
    assert subprocess.check_output([str(restored_home / ".local/bin/cli")], text=True) == "home-cli"
    assert (restored_home / ".local/bin/target-only").read_text() == "keep target CLI"
    assert (restored_home / ".local/share/state").read_text() == "keep home state"
    home_cmd = [cmd for cmd in commands if cmd[-1] == f"{restored_home / '.local/bin'}/"][0]
    assert "--chown=1000:1000" in home_cmd
    assert "--delete" not in home_cmd and "--delete-excluded" not in home_cmd
    assert "service_bot.local_bin.restore.request" in caplog.text
    assert "service_bot.local_bin.restore.success" in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_restore_old_artifact_leaves_home_cli_untouched(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifacts/3/openclaw"
    (artifact / ".local/bin").mkdir(parents=True)
    (artifact / ".local/bin/cli").write_text("old engine CLI")
    target_home = tmp_path / "restored-home"
    (target_home / ".local/bin").mkdir(parents=True)
    (target_home / ".local/bin/cli").write_text("home CLI")
    _, restore = _restore_service(monkeypatch, artifact, target_home)
    restore()
    assert (target_home / ".openclaw/.local/bin/cli").read_text() == "old engine CLI"
    assert (target_home / ".local/bin/cli").read_text() == "home CLI"
    assert "service_bot.local_bin.restore.skip" in caplog.text


@pytest.mark.parametrize("parent", [HOME_ADMIN_ARTIFACT_ROOT, HOME_ADMIN_ARTIFACT_ROOT + "/.local"])
def test_restore_rejects_symlink_artifact_parents(tmp_path, parent, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifact"
    link = artifact / parent
    link.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    run = MagicMock()
    with pytest.raises(ValueError, match="symlink"):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=tmp_path / "home",
            bot_id="restore-cli", version=3, run_command=run, timeout_seconds=30,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.restore.failure" in caplog.text
    assert "error_type=ValueError" in caplog.text


def test_restore_copy_failure_propagates_and_logs(tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifact"
    (artifact / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin").mkdir(parents=True)
    with pytest.raises(BotBuildMigrationError, match="copy failed"):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=tmp_path / "home",
            bot_id="restore-cli", version=3,
            run_command=MagicMock(side_effect=BotBuildMigrationError("copy failed")),
            timeout_seconds=30,
        )
    assert "service_bot.local_bin.restore.failure" in caplog.text
    assert "service_bot.local_bin.restore.success" not in caplog.text


@pytest.mark.parametrize("parent", [
    HOME_ADMIN_ARTIFACT_ROOT,
    HOME_ADMIN_ARTIFACT_ROOT + "/.local",
    HOME_ADMIN_ARTIFACT_ROOT + "/.local/bin",
])
def test_build_rejects_symlink_artifact_parents(tmp_path, parent, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifact"
    link = artifact / parent
    link.parent.mkdir(parents=True)
    link.symlink_to(tmp_path / "outside", target_is_directory=True)
    source_home = tmp_path / "source"
    (source_home / ".local/bin").mkdir(parents=True)
    run = MagicMock()
    with pytest.raises(ValueError, match="symlink"):
        sync_local_bin_artifact(
            source_home=source_home, target_dir=artifact,
            device_id="device-cli-test", version="3", build_ignore_paths=(),
            run_command=run,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.failure" in caplog.text


@pytest.mark.parametrize("parent", [HOME_ADMIN_ARTIFACT_ROOT, HOME_ADMIN_ARTIFACT_ROOT + "/.local"])
def test_restore_rejects_non_directory_artifact_parents(tmp_path, parent):
    artifact = tmp_path / "artifact"
    invalid = artifact / parent
    invalid.parent.mkdir(parents=True)
    invalid.write_text("invalid")
    with pytest.raises(NotADirectoryError):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=tmp_path / "home",
            bot_id="restore-cli", version=3, run_command=MagicMock(), timeout_seconds=30,
        )


@pytest.mark.parametrize("invalid", ["bin-file", "bin-link", "target-local-link", "target-bin-link"])
def test_restore_rejects_invalid_bin_or_target_parent(tmp_path, invalid, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifact"
    source_bin = artifact / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin"
    source_bin.parent.mkdir(parents=True)
    target_home = tmp_path / "home"
    target_home.mkdir()
    if invalid == "bin-file":
        source_bin.write_text("not a directory")
    elif invalid == "bin-link":
        source_bin.symlink_to("missing")
    else:
        source_bin.mkdir()
        link = target_home / (".local" if invalid == "target-local-link" else ".local/bin")
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(tmp_path / "outside", target_is_directory=True)
    run = MagicMock()
    with pytest.raises(ValueError, match="symlink|directory"):
        restore_local_bin_artifact(
            artifact_dir=artifact, target_home=target_home,
            bot_id="restore-cli", version=3, run_command=run, timeout_seconds=30,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.restore.failure" in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_restore_empty_bin_preserves_target_only_cli(monkeypatch, tmp_path):
    artifact = tmp_path / "artifacts/3/openclaw"
    (artifact / HOME_ADMIN_ARTIFACT_ROOT / ".local/bin").mkdir(parents=True)
    target_home = tmp_path / "home"
    (target_home / ".local/bin").mkdir(parents=True)
    (target_home / ".local/bin/target-only").write_text("preserved")
    _, restore = _restore_service(monkeypatch, artifact, target_home)
    restore()
    assert (target_home / ".local/bin/target-only").read_text() == "preserved"


@pytest.mark.parametrize("dangling_link", [False, True])
def test_non_directory_cli_source_fails(tmp_path, dangling_link):
    source = tmp_path / "home"
    (source / ".local").mkdir(parents=True)
    if dangling_link:
        (source / ".local/bin").symlink_to("missing-directory")
    else:
        (source / ".local/bin").write_text("not a directory")
    with pytest.raises(NotADirectoryError):
        sync_local_bin_artifact(
            source_home=source,
            target_dir=tmp_path / "artifact",
            device_id="device-cli-test",
            version="2",
            build_ignore_paths=(),
            run_command=MagicMock(),
        )


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_cli_broken_file_link_fails_migration(migration, caplog):
    caplog.set_level("INFO")
    source, _, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "cli").symlink_to("missing-cli-target")
    with pytest.raises(BotBuildMigrationError):
        migrate()
    assert "service_bot.local_bin.failure" in caplog.text
    assert "service_bot.local_bin.success" not in caplog.text
