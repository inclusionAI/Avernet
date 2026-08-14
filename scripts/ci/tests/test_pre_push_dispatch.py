from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def test_bcs_change_dispatches_unit_and_singlebox_coverage(tmp_path: Path):
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=dev")
    _git(repository, "config", "user.name", "CI Test")
    _git(repository, "config", "user.email", "ci-test@example.com")

    dispatcher = repository / "scripts/ci/pre_push.sh"
    dispatcher.parent.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts/ci/pre_push.sh", dispatcher)
    dispatcher.chmod(0o755)

    baseline = repository / "README.md"
    baseline.write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "baseline")
    base = _git(repository, "rev-parse", "HEAD")

    changed = repository / "src/bcs/feature.rs"
    changed.parent.mkdir(parents=True)
    changed.write_text("// BCS change\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "change BCS")

    result = subprocess.run(
        [
            str(dispatcher),
            "--base",
            base,
            "--head",
            "HEAD",
            "--dry-run",
        ],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert "src/bcs/scripts/ci_test.sh" in result.stdout
    assert "scripts/ci/singlebox_coverage.sh" in result.stdout
    assert "scripts/ci/verify_singlebox_coverage_artifacts.py" in result.stdout

    base = _git(repository, "rev-parse", "HEAD")
    reporter = repository / "scripts/ci/singlebox_coverage_report.py"
    reporter.write_text("# report change\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "change coverage reporter")

    result = subprocess.run(
        [str(dispatcher), "--base", base, "--head", "HEAD", "--dry-run"],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert "scripts/ci/singlebox_coverage.sh" in result.stdout
    assert "scripts/ci/verify_singlebox_coverage_artifacts.py" in result.stdout
    assert "src/bcs/scripts/ci_test.sh" not in result.stdout
