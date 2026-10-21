"""Disabled artifacts must never inspect draft CLI trees and must clear stale retries."""
from pathlib import Path
import subprocess
from unittest.mock import MagicMock
import pytest
from agentclaw.community.core.service_bot.services.agentclaw_artifact import sync_agentclaw_artifact
from agentclaw.community.core.service_bot.services.local_bin_artifact import sync_local_bin_artifact


@pytest.mark.parametrize("sync,relative", [(sync_agentclaw_artifact, ".agentclaw"), (sync_local_bin_artifact, ".local/bin")])
@pytest.mark.parametrize("stale", [True, False])
def test_disabled_never_probes_source_and_cleans_retry(tmp_path, monkeypatch, sync, relative, stale, caplog):
    caplog.set_level("INFO")
    source = tmp_path / "source"
    source.symlink_to(tmp_path / "missing")
    target = tmp_path / "version/openclaw"
    target.mkdir(parents=True)
    (target / "engine").write_text("keep")
    (target.parent / "other").write_text("keep")
    snapshot = target.parent / relative
    if stale:
        snapshot.mkdir(parents=True)
        (snapshot / "old").write_text("old")
    original = Path.lstat
    def guarded(path, *args, **kw):
        assert not path.is_relative_to(source), "disabled source was probed"
        return original(path, *args, **kw)
    monkeypatch.setattr(Path, "lstat", guarded)
    def run(**kw):
        subprocess.run(kw["cmd"][1:], check=True, capture_output=True)
    sync(source_home=source, target_dir=target, device_id="d", version="1",
         build_ignore_paths=(), run_command=run, enabled=False)
    assert not snapshot.exists() or list(snapshot.iterdir()) == []
    assert snapshot.exists() is stale
    assert (target / "engine").read_text() == "keep"
    assert (target.parent / "other").read_text() == "keep"
    assert "reason=feature_disabled" in caplog.text


@pytest.mark.parametrize("sync,relative", [(sync_agentclaw_artifact, ".agentclaw"), (sync_local_bin_artifact, ".local/bin")])
def test_disabled_cleanup_failure_propagates(tmp_path, sync, relative):
    snapshot = tmp_path / "version" / relative
    snapshot.mkdir(parents=True)
    with pytest.raises(RuntimeError, match="cleanup"):
        sync(source_home=tmp_path / "source", target_dir=tmp_path / "version/openclaw",
            device_id="d", version="1", build_ignore_paths=(), enabled=False,
            run_command=MagicMock(side_effect=RuntimeError("cleanup")))


@pytest.mark.parametrize("sync,relative", [(sync_agentclaw_artifact, ".agentclaw"), (sync_local_bin_artifact, ".local/bin")])
def test_enabled_then_disabled_same_version_removes_only_snapshot(tmp_path, sync, relative):
    source = tmp_path / "source"
    cli = source / relative / "tool"
    cli.parent.mkdir(parents=True)
    cli.write_text("payload")
    target = tmp_path / "version/openclaw"
    target.mkdir(parents=True)
    def run(**kw):
        subprocess.run(kw["cmd"][1:], check=True, capture_output=True)
    common = dict(source_home=source, target_dir=target, device_id="d", version="1",
                  build_ignore_paths=(), run_command=run)
    sync(**common, enabled=True)
    assert (target.parent / relative / "tool").exists()
    sync(**common, enabled=False)
    assert list((target.parent / relative).iterdir()) == []
    assert cli.read_text() == "payload"
