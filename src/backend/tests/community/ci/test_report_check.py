from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
REPORT_CHECK_PATH = REPOSITORY_ROOT / "scripts" / "ci" / "report_check.py"
SPEC = importlib.util.spec_from_file_location("report_check", REPORT_CHECK_PATH)
assert SPEC is not None and SPEC.loader is not None
report_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report_check)


def test_repository_relative_path_normalizes_absolute_source_root():
    source_root = REPOSITORY_ROOT / "src" / "baas" / "packages" / "community" / "src"

    actual = report_check.repository_relative_path(source_root, REPOSITORY_ROOT)

    assert actual == Path("src/baas/packages/community/src")


def test_find_repository_root_uses_nearest_worktree_marker(tmp_path):
    worktree = tmp_path / "worktree"
    source_root = worktree / "src" / "baas"
    source_root.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: /tmp/example\n")

    assert report_check.find_repository_root(source_root) == worktree


def test_clean_git_environment_removes_hook_repository_overrides(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/main/.git/worktrees/feature")
    monkeypatch.setenv("GIT_WORK_TREE", "/main")
    monkeypatch.setenv("GIT_PREFIX", "src/baas/")
    monkeypatch.setenv("GIT_INDEX_FILE", "/main/.git/index")

    actual = report_check.clean_git_environment()

    assert "GIT_DIR" not in actual
    assert "GIT_WORK_TREE" not in actual
    assert "GIT_PREFIX" not in actual
    assert "GIT_INDEX_FILE" not in actual


def test_find_coverage_hits_prefers_source_relative_path_over_duplicate_basename():
    source_root = REPOSITORY_ROOT / "src" / "baas" / "packages" / "community" / "src"
    expected_hits = {55: 1}
    coverage_hits = {
        "secbaas/other/_workspace.py": {55: 0, 56: 0},
        "secbaas/plugins/sandbox/arca/local_proc/_workspace.py": expected_hits,
    }

    actual = report_check.find_coverage_hits(
        "src/baas/packages/community/src/secbaas/plugins/sandbox/arca/local_proc/_workspace.py",
        coverage_hits,
        source_root,
        REPOSITORY_ROOT,
    )

    assert actual is expected_hits


def test_find_coverage_hits_rejects_ambiguous_basename_fallback():
    coverage_hits = {
        "first/_workspace.py": {1: 1},
        "second/_workspace.py": {1: 0},
    }

    actual = report_check.find_coverage_hits(
        "unrelated/root/_workspace.py",
        coverage_hits,
        REPOSITORY_ROOT / "missing-source-root",
        REPOSITORY_ROOT,
    )

    assert actual is None
