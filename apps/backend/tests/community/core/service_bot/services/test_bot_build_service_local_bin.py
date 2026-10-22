"""CLI snapshots copy independently of the engine root and retry cleanly."""

from agentclaw.community.core.bot_management.engines.artifact_transfer import ArtifactTransferRegistry

import shutil
import os
from pathlib import Path
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
        assert cmd[1] != "rm"
        if cmd[1] == "chmod":
            return
        result = subprocess.run(cmd[1:], capture_output=True, text=True)
        if result.returncode:
            raise BotBuildMigrationError(result.stderr)

    service._run_local_command = run_command

    def migrate(rules=(), *, enabled=True):
        return service._migrate_bot_instance(
            device_id="device-cli-test",
            source_dir=source,
            target_dir=target,
            version_str="2",
            is_nas=True,
            nas_storage_id=source.parent,
            build_ignore_paths=rules,
            **({"include_runtime_artifacts": enabled} if enabled is not None else {}),
        )

    return source, target, commands, migrate


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_migration_copies_regular_executable_and_skips_link(migration, caplog):
    caplog.set_level("INFO")
    source, target, commands, migrate = migration
    local = source.parent / ".local"
    (local / "bin").mkdir(parents=True)
    tools = local / "share/tools"
    tools.mkdir(parents=True)
    tool = tools / "cli"
    tool.write_text("#!/bin/sh\nprintf 'cli works'\n")
    tool.chmod(0o755)
    (local / "bin/linked-cli").symlink_to("../share/tools/cli")
    shutil.copy2(tool, local / "bin/cli")
    (local / "credentials").write_text("private-source-content")

    assert migrate()

    cli = target.parent / ".local/bin/cli"
    assert cli.is_file() and not cli.is_symlink()
    assert cli.stat().st_mode & 0o111 == 0o111
    assert subprocess.check_output([str(cli)], text=True) == "cli works"
    assert not (target.parent / ".local/share").exists()
    assert not (target.parent / ".local/credentials").exists()
    assert (target / "config.json").read_text() == "{}"
    assert not (target.parent / ".local/bin/linked-cli").exists()
    assert any("--no-links" in cmd for cmd in commands)
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
    assert (target.parent / ".local/bin/cli").exists()

    assert migrate((rule,))
    assert (target.parent / ".local/bin").is_dir()
    assert list((target.parent / ".local/bin").iterdir()) == []


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_migration_child_ignore_omits_only_selected_cli(migration):
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    (source_bin / "private").mkdir(parents=True)
    (source_bin / "private/cli").write_text("skip")
    (source_bin / "cli").write_text("keep")
    (source_bin / "skip-cli").write_text("skip")
    assert migrate((".local/bin/private", ".local/bin/skip-cli"))
    assert (target.parent / ".local/bin/cli").read_text() == "keep"
    assert not (target.parent / ".local/bin/private").exists()
    assert not (target.parent / ".local/bin/skip-cli").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("name", ["cfuse", "cfuse-claude", "cfuse-codex"])
def test_migration_skips_runtime_managed_cfuse_wrapper(migration, name):
    source, artifact, _, migrate = migration
    source_home = source.parent
    source_bin = source_home / ".local/bin"
    source_bin.mkdir(parents=True)
    tool_dir = source_home / ".local/tools"
    tool_dir.mkdir()
    (tool_dir / "wrapped").write_text("runtime-managed")
    (source_bin / "keep").write_text("#!/bin/sh\nprintf 'keep'\n")
    (source_bin / name).symlink_to("../tools/wrapped")

    assert migrate()
    assert (artifact.parent / ".local/bin/keep").is_file()
    assert not (artifact.parent / f".local/bin/{name}").exists()


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
    assert (target.parent / ".local/bin").is_dir()
    assert list((target.parent / ".local/bin").iterdir()) == []
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
    service._artifact_transfers = ArtifactTransferRegistry({})
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
    agentclaw = source.parent / ".agentclaw"
    agentclaw.mkdir()
    (agentclaw / "cli").write_text("complete home CLI")
    engine_bin = source / ".local/bin"
    home_bin = source.parent / ".local/bin"
    engine_bin.mkdir(parents=True)
    home_bin.mkdir(parents=True)
    (engine_bin / "cli").write_text("engine-cli")
    (source / ".local/engine-data").write_text("engine state")
    (home_bin / "cli").write_text("#!/bin/sh\nprintf 'home-cli'\n")
    (home_bin / "cli").chmod(0o755)
    assert migrate()
    assert (artifact.parent / ".agentclaw/cli").read_text() == "complete home CLI"
    assert (artifact / ".local/bin/cli").read_text() == "engine-cli"
    assert (artifact / ".local/engine-data").read_text() == "engine state"

    restored_home = tmp_path / "restored-home"
    (restored_home / ".local/bin").mkdir(parents=True)
    (restored_home / ".local/bin/cli").write_text("previous home CLI")
    (restored_home / ".local/bin/target-only").write_text("keep target CLI")
    (restored_home / ".local/share").mkdir()
    (restored_home / ".local/share/state").write_text("keep home state")
    # Only bin can leave the version root, even if other Home data exists.
    (artifact.parent / ".local/share").mkdir()
    (artifact.parent / ".local/share/state").write_text("do not restore")
    commands, restore = _restore_service(monkeypatch, artifact, restored_home)
    assert restore()["restore_type"] == "migration_path"
    assert (restored_home / ".agentclaw/cli").read_text() == "complete home CLI"
    assert (restored_home / ".openclaw/.local/bin/cli").read_text() == "engine-cli"
    assert (restored_home / ".openclaw/.local/engine-data").read_text() == "engine state"
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


@pytest.mark.parametrize("parent", [".", ".local"])
def test_restore_rejects_symlink_artifact_parents(tmp_path, parent, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    link = artifact.parent / parent
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


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_restore_preserves_runtime_managed_cfuse_wrappers(tmp_path):
    artifact = tmp_path / "versions/3/openclaw"
    artifact.mkdir(parents=True)
    artifact_bin = artifact.parent / ".local/bin"
    artifact_bin.mkdir(parents=True)
    (artifact_bin / "keep").write_text("published")
    for name in ("cfuse", "cfuse-claude", "cfuse-codex"):
        (artifact_bin / name).write_text("published wrapper")

    target_home = tmp_path / "home"
    target_bin = target_home / ".local/bin"
    target_bin.mkdir(parents=True)
    (target_bin / "keep").write_text("originally local")
    for name in ("cfuse", "cfuse-claude", "cfuse-codex"):
        (target_bin / name).write_text("runtime wrapper")

    def run_command(**kwargs):
        cmd = kwargs["cmd"]
        result = subprocess.run(
            [arg for arg in cmd[1:] if not arg.startswith("--chown=")],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise BotBuildMigrationError(result.stderr)

    restore_local_bin_artifact(
        artifact_dir=artifact,
        target_home=target_home,
        bot_id="restore-cli",
        version=3,
        run_command=run_command,
        timeout_seconds=30,
    )
    assert (target_bin / "keep").read_text() == "published"
    for name in ("cfuse", "cfuse-claude", "cfuse-codex"):
        assert (target_bin / name).read_text() == "runtime wrapper"


def test_restore_copy_failure_propagates_and_logs(tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "artifact"
    (artifact.parent / ".local/bin").mkdir(parents=True)
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
    ".", ".local", ".local/bin",
])
def test_build_rejects_symlink_artifact_parents(tmp_path, parent, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    link = artifact.parent / parent
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


@pytest.mark.parametrize("parent", [".", ".local"])
def test_restore_rejects_non_directory_artifact_parents(tmp_path, parent):
    artifact = tmp_path / "versions/3/openclaw"
    invalid = artifact.parent / parent
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
    source_bin = artifact.parent / ".local/bin"
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
    artifact.mkdir(parents=True)
    (artifact.parent / ".local/bin").mkdir(parents=True)
    target_home = tmp_path / "home"
    (target_home / ".local/bin").mkdir(parents=True)
    (target_home / ".local/bin/target-only").write_text("preserved")
    _, restore = _restore_service(monkeypatch, artifact, target_home)
    restore()
    assert (target_home / ".local/bin/target-only").read_text() == "preserved"


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_retry_cleans_only_version_bin_and_keeps_other_version_data(migration):
    source, artifact, commands, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "old-cli").write_text("old")
    assert migrate()
    version_local = artifact.parent / ".local"
    (version_local / "share").mkdir()
    (version_local / "share/state").write_text("preserve other version data")
    (source_bin / "old-cli").unlink()
    (source_bin / "new-cli").write_text("new")
    assert migrate()
    assert not (version_local / "bin/old-cli").exists()
    assert (version_local / "bin/new-cli").read_text() == "new"
    assert (version_local / "share/state").read_text() == "preserve other version data"
    assert all(cmd[1] != "rm" for cmd in commands)
    cli_copies = [cmd for cmd in commands if cmd[-1] == f"{version_local / 'bin'}/"]
    assert len(cli_copies) == 2
    assert all(cmd[-2] == f"{source_bin}/" for cmd in cli_copies)


def test_retry_cleanup_failure_aborts_before_copy(tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    (artifact.parent / ".local/bin").mkdir(parents=True)
    source_home = tmp_path / "home"
    run = MagicMock(side_effect=BotBuildMigrationError("cleanup failed"))
    with pytest.raises(BotBuildMigrationError, match="cleanup failed"):
        sync_local_bin_artifact(
            source_home=source_home, target_dir=artifact,
            device_id="device-cli-test", version="3", build_ignore_paths=(),
            run_command=run,
        )
    assert run.call_count == 1
    assert run.call_args.kwargs["command_name"] == "rsync local bin artifact cleanup"
    assert run.call_args.kwargs["cmd"][1] == "rsync"
    assert "service_bot.local_bin.cleanup.request" in caplog.text
    assert "cleanup_method=rsync" in caplog.text
    assert "service_bot.local_bin.failure" in caplog.text
    assert "service_bot.local_bin.success" not in caplog.text
    assert "service_bot.local_bin.skip" not in caplog.text
    assert "service_bot.local_bin.cleanup.success" not in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("state", ["empty", "missing", "excluded"])
def test_retry_empty_or_absent_payload_clears_only_bin_using_rsync(migration, caplog, state):
    caplog.set_level("INFO")
    source, artifact, commands, migrate = migration
    source_bin = source.parent / ".local/bin"
    source_bin.mkdir(parents=True)
    (source_bin / "old-cli").write_text("old")
    (source / ".local").mkdir()
    (source / ".local/state").write_text("engine state")
    assert migrate()
    version_local = artifact.parent / ".local"
    version_bin = version_local / "bin"
    version_bin.chmod(0o750)
    (version_local / "share").mkdir()
    (version_local / "share/state").write_text("keep other data")
    first_command = len(commands)
    if state == "empty":
        (source_bin / "old-cli").unlink()
    elif state == "missing":
        shutil.rmtree(source_bin)
    rules = (".local/bin",) if state == "excluded" else ()
    assert migrate(rules)
    assert version_bin.is_dir() and list(version_bin.iterdir()) == []
    assert (version_local / "share/state").read_text() == "keep other data"
    assert (artifact / ".local/state").read_text() == "engine state"
    assert all(cmd[1] != "rm" for cmd in commands)
    cli_sync = [cmd for cmd in commands[first_command:] if cmd[-1] == f"{version_bin}/"]
    assert len(cli_sync) == 1
    assert "--delete" in cli_sync[0] and "--delete-excluded" in cli_sync[0]
    if state == "empty":
        assert cli_sync[0][-2] == f"{source_bin}/"
        assert "service_bot.local_bin.cleanup.request" not in caplog.text
    else:
        assert not Path(cli_sync[0][-2]).exists()
        assert version_bin.stat().st_mode & 0o777 == 0o750
        assert "service_bot.local_bin.cleanup.request" in caplog.text
        assert "service_bot.local_bin.cleanup.success" in caplog.text
        assert "cleanup_method=rsync" in caplog.text


@pytest.mark.parametrize("excluded", [False, True])
def test_absent_target_without_payload_does_not_create_bin_or_run_cleanup(tmp_path, excluded):
    source_home = tmp_path / "home"
    if excluded:
        (source_home / ".local/bin").mkdir(parents=True)
        (source_home / ".local/bin/cli").write_text("ignored")
    artifact = tmp_path / "versions/3/openclaw"
    run = MagicMock()
    sync_local_bin_artifact(
        source_home=source_home, target_dir=artifact,
        device_id="device-cli-test", version="3",
        build_ignore_paths=(".local/bin",) if excluded else (), run_command=run,
    )
    run.assert_not_called()
    assert not (artifact.parent / ".local/bin").exists()


def test_build_rejects_non_directory_version_bin(tmp_path, caplog):
    caplog.set_level("INFO")
    artifact = tmp_path / "versions/3/openclaw"
    (artifact.parent / ".local").mkdir(parents=True)
    (artifact.parent / ".local/bin").write_text("not a directory")
    run = MagicMock()
    with pytest.raises(NotADirectoryError, match="bin"):
        sync_local_bin_artifact(
            source_home=tmp_path / "home", target_dir=artifact,
            device_id="device-cli-test", version="3", build_ignore_paths=(),
            run_command=run,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.failure" in caplog.text


def test_non_directory_cli_source_fails(tmp_path):
    source = tmp_path / "home"
    (source / ".local").mkdir(parents=True)
    (source / ".local/bin").write_text("not a directory")
    with pytest.raises(NotADirectoryError):
        sync_local_bin_artifact(
            source_home=source, target_dir=tmp_path / "artifact",
            device_id="device-cli-test", version="2", build_ignore_paths=(),
            run_command=MagicMock(),
        )


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_cli_links_skip_and_retry_removes_literal_same_name_files(migration, tmp_path, caplog):
    caplog.set_level("INFO")
    source, artifact, commands, migrate = migration
    source_bin = source.parent / ".local/bin"
    (source_bin / "nested").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("outside-secret")
    names = ["node", "npm", "harness", "broken", "directory", "nested/link", "literal*?[x]", r"back\slash*", r"plain\slash", "only[x]", "question?", "star*"]
    for name in names:
        (source_bin / name).write_text("previous")
    (source_bin / "literalZZx").write_text("keep literal neighbour")
    assert migrate()
    for name in names:
        path = source_bin / name
        path.unlink()
        path.symlink_to(outside if name == "directory" else ("missing" if name == "broken" else outside / "sentinel"))
    assert migrate()
    target_bin = artifact.parent / ".local/bin"
    for name in names:
        assert not (target_bin / name).exists()
        assert not (target_bin / name).is_symlink()
        assert (source_bin / name).is_symlink()
    assert (target_bin / "literalZZx").read_text() == "keep literal neighbour"
    assert (outside / "sentinel").read_text() == "outside-secret"
    assert "outside-secret" not in caplog.text
    assert "symlink_policy=skip" in caplog.text
    assert "service_bot.local_bin.symlink.skip" in caplog.text
    cli_commands = [cmd for cmd in commands if cmd[-1] == f"{target_bin}/"]
    assert all("--no-links" in cmd and "-av" in cmd and "-avL" not in cmd for cmd in cli_commands)


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
@pytest.mark.parametrize("parent", [".", ".local", ".local/bin"])
@pytest.mark.parametrize("dangling", [False, True])
def test_source_symlink_parent_skips_and_cleans_old_bin(tmp_path, caplog, parent, dangling):
    caplog.set_level("INFO")
    source_home = tmp_path / "home"
    outside = tmp_path / "outside"
    (outside / ".local/bin").mkdir(parents=True)
    (outside / ".local/bin/sentinel").write_text("never copy")
    link = source_home / parent
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "missing" if dangling else outside, target_is_directory=True)
    artifact = tmp_path / "versions/3/openclaw"
    target_bin = artifact.parent / ".local/bin"
    target_bin.mkdir(parents=True)
    (target_bin / "stale").write_text("stale")
    commands = []

    def run_command(**kwargs):
        cmd = kwargs["cmd"]
        commands.append(cmd)
        subprocess.run(cmd[1:], check=True, capture_output=True)

    sync_local_bin_artifact(
        source_home=source_home, target_dir=artifact, device_id="device-cli-test",
        version="3", build_ignore_paths=(), run_command=run_command,
    )
    assert list(target_bin.iterdir()) == []
    assert len(commands) == 1 and commands[0][-2] != f"{source_home / '.local/bin'}/"
    assert "reason=source_symlink" in caplog.text
    assert link.is_symlink()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_nested_relative_directory_and_cycle_links_are_never_followed(migration, monkeypatch):
    source, artifact, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    (source_bin / "nested").mkdir(parents=True)
    (source_bin / "nested/regular").write_text("regular")
    (source_bin / "nested").chmod(0o750)
    (source_bin / "alias").symlink_to("nested", target_is_directory=True)
    (source_bin / "nested/cycle").symlink_to("..", target_is_directory=True)
    (source_bin / "relative").symlink_to("nested/regular")
    (source_bin / "ignored").mkdir()
    (source_bin / "ignored/broken").symlink_to("missing")
    # A scan or rsync which dereferences links would read or copy extra data.
    monkeypatch.setattr(Path, "readlink", MagicMock(side_effect=AssertionError("must not read link target")))
    assert migrate((".local/bin/ignored",))
    target_bin = artifact.parent / ".local/bin"
    assert (target_bin / "nested/regular").read_text() == "regular"
    assert (target_bin / "nested").stat().st_mode & 0o777 == 0o750
    assert sorted(path.relative_to(target_bin).as_posix() for path in target_bin.rglob("*")) == ["nested", "nested/regular"]


def test_source_scan_io_failure_propagates_and_logs(tmp_path, monkeypatch, caplog):
    from agentclaw.community.core.service_bot.services import local_bin_artifact

    caplog.set_level("INFO")
    source_home = tmp_path / "home"
    (source_home / ".local/bin").mkdir(parents=True)
    run = MagicMock()
    monkeypatch.setattr(local_bin_artifact.os, "scandir", MagicMock(side_effect=PermissionError("scan denied")))
    with pytest.raises(PermissionError, match="scan denied"):
        sync_local_bin_artifact(
            source_home=source_home, target_dir=tmp_path / "versions/3/openclaw",
            device_id="device-cli-test", version="3", build_ignore_paths=(), run_command=run,
        )
    run.assert_not_called()
    assert "service_bot.local_bin.failure" in caplog.text
    assert "error_type=PermissionError" in caplog.text
    assert "service_bot.local_bin.success" not in caplog.text


def test_absent_artifact_source_link_skips_without_creating_target(tmp_path, caplog):
    caplog.set_level("INFO")
    source_home = tmp_path / "home"
    source_home.symlink_to(tmp_path / "missing")
    run = MagicMock()
    target = tmp_path / "versions/3/openclaw"
    sync_local_bin_artifact(
        source_home=source_home, target_dir=target, device_id="device-cli-test",
        version="3", build_ignore_paths=(), run_command=run,
    )
    run.assert_not_called()
    assert not target.parent.exists()
    assert "reason=source_symlink" in caplog.text


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_skip_directory_link_removes_old_directory_and_omits_fifo(migration, tmp_path):
    source, artifact, commands, migrate = migration
    source_bin = source.parent / ".local/bin"
    (source_bin / "directory").mkdir(parents=True)
    (source_bin / "directory/old").write_text("previous")
    assert migrate()
    shutil.rmtree(source_bin / "directory")
    (source_bin / "directory").symlink_to(tmp_path / "missing", target_is_directory=True)
    os.mkfifo(source_bin / "pipe")
    assert migrate()
    target_bin = artifact.parent / ".local/bin"
    assert list(target_bin.iterdir()) == []
    assert (source_bin / "directory").is_symlink()
    assert (source_bin / "pipe").exists()
    assert "--no-devices" in commands[-1] and "--no-specials" in commands[-1]


def test_migration_defaults_to_skipping_both_cli_snapshots(migration):
    source, target, commands, migrate = migration
    for relative in (".agentclaw/tool", ".local/bin/tool"):
        tool = source.parent / relative
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text("payload")
    assert migrate(enabled=None)
    assert (target / "config.json").exists()
    assert not (target.parent / ".agentclaw").exists()
    assert not (target.parent / ".local").exists()
