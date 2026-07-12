from __future__ import annotations

import importlib.util
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
REPORT_CHECK_PATH = REPOSITORY_ROOT / "scripts" / "ci" / "report_check.py"
SPEC = importlib.util.spec_from_file_location("report_check", REPORT_CHECK_PATH)
assert SPEC is not None and SPEC.loader is not None
report_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report_check)


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
