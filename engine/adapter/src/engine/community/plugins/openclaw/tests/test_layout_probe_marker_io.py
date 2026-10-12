"""Transient active-marker I/O must never masquerade as marker absence."""
import errno
import os
from pathlib import Path

from engine.community.plugins.openclaw.layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)
from engine.community.plugins.openclaw.tests.test_layout_probe import (
    _ready_home,
    _write_active_marker,
)


def test_active_marker_os_error_is_not_treated_as_absent(tmp_path, monkeypatch):
    home, _, _, pool_repo = _ready_home(tmp_path)
    active_marker = _write_active_marker(home)
    (home / ".openclaw/workspace/skills-pool/.pool-ready").unlink()
    original_stat = os.stat
    original_lstat = os.lstat

    def fail_active_marker_stat(path, *args, **kwargs):
        if Path(path) == active_marker:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_stat(path, *args, **kwargs)

    def fail_active_marker_lstat(path, *args, **kwargs):
        if Path(path) == active_marker:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", fail_active_marker_stat)
    monkeypatch.setattr(os, "lstat", fail_active_marker_lstat)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "active_marker_temporarily_unavailable"
    assert result.evidence["marker"] == str(active_marker)


def test_active_marker_read_io_error_is_transient(tmp_path, monkeypatch):
    home, _, _, pool_repo = _ready_home(tmp_path)
    active_marker = _write_active_marker(home)
    original_read_bytes = Path.read_bytes

    def fail_active_marker_read(path):
        if path == active_marker:
            raise OSError(errno.ESTALE, "stale NAS handle")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_active_marker_read)

    result = inspect_runtime_layout(
        engine="openclaw",
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_is_mounted=lambda path: path == pool_repo,
    )

    assert result.status is RuntimeLayoutInspectionStatus.TRANSIENT_ERROR
    assert result.evidence["reason"] == "active_marker_temporarily_unavailable"
