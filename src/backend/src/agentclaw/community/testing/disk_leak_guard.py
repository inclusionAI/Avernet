"""Belt-and-suspenders guard against file-backed SQLite leaks.

After Group A, ``plugins/local/database.py`` is structurally
in-memory only — file-backed SQLite cannot be constructed from the
production path. This guard catches any **future** regression that
re-introduces a file-backed branch (or any other code path that
writes one of the historical SQLite files to the repo root).

The pure helper :func:`find_leaked_sqlite_files` is unit-tested in
isolation; the ``pytest_sessionfinish`` integration that calls it
lives in ``tests/conftest.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


# Filenames historically used by file-backed local-mode SQLite.
# Any of these appearing at the search root after a test run means
# something bypassed the in-memory-only contract.
_KNOWN_LEAK_NAMES: tuple[str, ...] = ("backend.db", "device.db")


def find_leaked_sqlite_files(
    root: Path,
    names: Iterable[str] = _KNOWN_LEAK_NAMES,
) -> list[Path]:
    """Return absolute paths of any known SQLite leak files found at ``root``.

    The check is intentionally **shallow** (no recursive walk): the
    bug shape we're catching is "production code wrote ``./backend.db``
    relative to cwd," not "some test fixture spilled a DB into a
    nested tmp_path." Recursing would create noisy false positives
    against the legitimate ``tmp_path``-based test fixtures still in
    the suite.

    Returns an empty list when the directory is clean.
    """
    leaked: list[Path] = []
    for name in names:
        candidate = root / name
        if candidate.exists():
            leaked.append(candidate.resolve())
    return leaked
