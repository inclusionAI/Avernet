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


def test_real_runner_forwards_repair_fixture_and_propagates_pytest_failure(tmp_path):
    """Exercise shell control flow with a failing pytest process that writes JUnit."""
    import os
    import shlex
    import subprocess
    import sys

    backend = tmp_path / "apps/backend"
    backend.mkdir(parents=True)
    reports = tmp_path / "reports"
    reports.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, pathlib\n"
        "assert os.environ['SINGLEBOX_ACCEPTANCE_REPAIR_PROVIDER_FILE'].endswith('provider.json')\n"
        "args=sys.argv\n"
        "report=pathlib.Path(args[args.index('--junitxml')+1])\n"
        "report.write_text('<testsuites><testsuite tests=\"1\" failures=\"1\" errors=\"0\" skipped=\"0\" time=\"1\">'\n"
        "                  '<testcase name=\"failure\"><failure/></testcase></testsuite></testsuites>')\n"
        "raise SystemExit(1)\n"
    )
    uv.chmod(0o755)
    source = RUNNER.read_text()
    function = source.split("run_acceptance_smoke() {", 1)[1].split("\nrun_bcs_e2e() {", 1)[0]
    q = shlex.quote
    for targets in [
        "tests/community/acceptance/collaboration_repair",
        "tests/community/acceptance/bot_ownership_sync",
        "tests/community/acceptance/collaboration_repair tests/community/acceptance/bot_ownership_sync",
    ]:
        script = f"""set -euo pipefail
repo_root={q(str(tmp_path))}
report_dir={q(str(reports))}
script_dir={q(str(REPO_ROOT / 'singlebox/ci'))}
bcs_coverage_dir={q(str(tmp_path / 'bcs'))}
coverage_standalone_runtime={q(str(tmp_path / 'runtime'))}
repair_provider_fixture={q(str(tmp_path / 'provider.json'))}
reporter_command=({q(sys.executable)})
acceptance_targets=({targets})
run_acceptance_smoke() {{{function}
run_acceptance_smoke
"""
        result = subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True,
            env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert (reports / "acceptance-junit.xml").is_file()
