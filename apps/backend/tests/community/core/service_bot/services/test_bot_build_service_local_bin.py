"""CLI snapshots copy independently of the engine root and retry cleanly."""

import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.services.bot_build_service import (
    BotBuildMigrationError,
    BotBuildService,
)
from agentclaw.community.core.service_bot.services.local_bin_artifact import (
    sync_local_bin_artifact,
)


@pytest.fixture
def migration(tmp_path):
    source = tmp_path / "home/.openclaw"
    source.mkdir(parents=True)
    (source / "config.json").write_text("{}")
    target = tmp_path / "artifact"
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

    cli = target / ".local/bin/cli"
    assert cli.is_file() and not cli.is_symlink()
    assert cli.stat().st_mode & 0o111 == 0o111
    assert subprocess.check_output([str(cli)], text=True) == "cli works"
    assert not (target / ".local/share").exists()
    assert not (target / ".local/credentials").exists()
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
    assert (target / ".local/bin/cli").exists()

    assert migrate((rule,))
    assert not (target / ".local").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync is required")
def test_migration_child_ignore_omits_only_selected_cli(migration):
    source, target, _, migrate = migration
    source_bin = source.parent / ".local/bin"
    (source_bin / "private").mkdir(parents=True)
    (source_bin / "private/cli").write_text("skip")
    (source_bin / "cli").write_text("keep")
    (source_bin / "skip-cli").write_text("skip")
    assert migrate((".local/bin/private", ".local/bin/skip-cli"))
    assert (target / ".local/bin/cli").read_text() == "keep"
    assert not (target / ".local/bin/private").exists()
    assert not (target / ".local/bin/skip-cli").exists()


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
    assert not (target / ".local").exists()
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
