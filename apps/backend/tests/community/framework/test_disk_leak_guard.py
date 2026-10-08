"""Unit tests for the disk-leak guard's pure helper.

The ``pytest_sessionfinish`` integration is hard to test in-process
(it's invoked by pytest itself at the end of a run), so we factor the
detection logic into ``find_leaked_sqlite_files`` and unit-test that.
"""
from __future__ import annotations

from pathlib import Path

from agentclaw.community.testing.disk_leak_guard import find_leaked_sqlite_files


def test_clean_directory_reports_no_leak(tmp_path: Path) -> None:
    """An empty cwd must yield zero leaks — the happy case after every
    test session in the post-Group-A world.
    """
    assert find_leaked_sqlite_files(tmp_path) == []


def test_detects_backend_db(tmp_path: Path) -> None:
    """A ``backend.db`` at the search root must be reported."""
    leaked_file = tmp_path / "backend.db"
    leaked_file.write_bytes(b"")
    found = find_leaked_sqlite_files(tmp_path)
    assert leaked_file.resolve() in found


def test_detects_device_db(tmp_path: Path) -> None:
    """Same for ``device.db`` — the other historical leak filename."""
    leaked_file = tmp_path / "device.db"
    leaked_file.write_bytes(b"")
    found = find_leaked_sqlite_files(tmp_path)
    assert leaked_file.resolve() in found


def test_detects_multiple_leaks(tmp_path: Path) -> None:
    (tmp_path / "backend.db").write_bytes(b"")
    (tmp_path / "device.db").write_bytes(b"")
    found = find_leaked_sqlite_files(tmp_path)
    assert len(found) == 2
    names = {p.name for p in found}
    assert names == {"backend.db", "device.db"}


def test_does_not_recurse_into_subdirs(tmp_path: Path) -> None:
    """A ``backend.db`` deep in a subdirectory (e.g. inside pytest's
    ``tmp_path`` fixtures) is not a production-code leak and must not
    be reported. Catching those would create noisy false positives.
    """
    nested_dir = tmp_path / "nested" / "deeper"
    nested_dir.mkdir(parents=True)
    (nested_dir / "backend.db").write_bytes(b"")
    assert find_leaked_sqlite_files(tmp_path) == []


def test_accepts_custom_names_for_extensibility(tmp_path: Path) -> None:
    """If a new local-mode SQLite filename is ever introduced, the
    helper can be re-pointed without code changes by passing
    ``names=``. Pin this for future maintainers.
    """
    (tmp_path / "future.db").write_bytes(b"")
    assert find_leaked_sqlite_files(tmp_path, names=("future.db",)) != []
    # Default name list still ignores it (the bug we're guarding
    # against is specifically the historical filenames).
    assert find_leaked_sqlite_files(tmp_path) == []
