from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "singlebox/ci/singlebox_coverage.sh"


def test_acceptance_runner_splits_owner_handoff_and_merges_junit_reports():
    source = RUNNER.read_text(encoding="utf-8")

    assert "tests/community/acceptance/bot_ownership_sync" in source
    assert "SINGLEBOX_ACCEPTANCE_OWNER_WORKERS" in source
    assert "--dist=load" in source
    assert "acceptance-serial-junit.xml" in source
    assert "acceptance-owner-junit.xml" in source
    assert "merge_junit_reports.py" in source
    assert "acceptance-junit.xml" in source


def test_acceptance_runner_propagates_both_subprocess_statuses():
    source = RUNNER.read_text(encoding="utf-8")

    assert "serial_status" in source
    assert "owner_status" in source
    assert "rc=1" in source
