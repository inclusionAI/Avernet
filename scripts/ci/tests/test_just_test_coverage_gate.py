from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER_PATH = REPO_ROOT / "scripts/lib/resolve_base_ref.sh"

# ci_test.sh is the entrypoint GitHub CI and local devs share for each Python
# module (see .github/workflows/unit-tests.yml). backend/engine have no justfile,
# so ci_test.sh is their only local surface; baas/gateway also expose `just test-ci`.
CI_TEST = {
    "backend": REPO_ROOT / "src/backend/scripts/ci_test.sh",
    "baas": REPO_ROOT / "src/baas/scripts/ci_test.sh",
    "engine": REPO_ROOT / "src/engine/scripts/ci_test.sh",
    "gateway": REPO_ROOT / "src/gateway/scripts/ci_test.sh",
}

# Changed-line coverage threshold each ci_test.sh passes to report_check.py
# when --base is set (CI always sets it; locally it is auto-derived).
CI_CHANGE_LINE_COVERAGE = {"backend": "80", "baas": "90", "engine": "90", "gateway": "90"}


def _run(
    *command: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _git(repository: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return _run("git", *args, cwd=repository, env=env, check=True).stdout.strip()


def _write(repository: Path, relative_path: str, content: str) -> None:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _clean_git_env(env: dict[str, str]) -> dict[str, str]:
    # Mirrors scripts/ci/report_check.py clean_git_environment: the helper is
    # also expected to work from a git worktree.
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_INDEX_FILE"):
        env.pop(name, None)
    return env


class ResolveBaseRefTest(unittest.TestCase):
    """Unit covers scripts/lib/resolve_base_ref.sh, the helper each ci_test.sh
    sources to derive the local changed-line coverage base ref."""

    def _make_remote_and_repo(self, root: Path, dev_commits: int = 1) -> tuple[Path, Path]:
        remote = root / "remote.git"
        publisher = root / "publisher"
        developer = root / "developer"

        remote.mkdir()
        _git(remote, "init", "--bare")

        publisher.mkdir()
        _git(publisher, "init", "--initial-branch=dev")
        _git(publisher, "config", "user.name", "CI Test")
        _git(publisher, "config", "user.email", "ci-test@example.com")
        _write(publisher, "README.md", "baseline\n")
        _git(publisher, "add", ".")
        _git(publisher, "commit", "-m", "baseline")
        _git(publisher, "remote", "add", "origin", str(remote))
        _git(publisher, "push", "-u", "origin", "dev")

        _run("git", "clone", "--branch", "dev", str(remote), str(developer), check=True)
        _git(developer, "config", "user.name", "CI Test")
        _git(developer, "config", "user.email", "ci-test@example.com")
        _write(developer, "src/baas/feature.txt", "intended change\n")
        _git(developer, "add", ".")
        _git(developer, "commit", "-m", "feature change")
        return remote, developer

    def _resolve(self, developer: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        effective_env = _clean_git_env((env or {}).copy())
        effective_env.setdefault("HOME", str(developer))
        # Resolve relative to the freshly created developer checkout.
        return _run(
            "bash",
            "-c",
            f"set -e; source {HELPER_PATH}; resolve_base_ref",
            cwd=developer,
            env=effective_env,
        )

    def test_helper_exists_and_defines_resolve_base_ref(self) -> None:
        self.assertTrue(HELPER_PATH.is_file(), f"missing helper: {HELPER_PATH}")
        text = HELPER_PATH.read_text(encoding="utf-8")
        self.assertIn("resolve_base_ref()", text)
        self.assertIn("resolve_merge_target()", text)
        self.assertIn("origin/dev", text)

    def test_resolve_default_origin_dev_returns_merge_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, developer = self._make_remote_and_repo(root)
            expected = _git(developer, "merge-base", "HEAD", "origin/dev")

            env = _clean_git_env(os.environ.copy())
            # The default path must not be perturbed by a runner-provided override.
            env.pop("AVERNET_PRE_PUSH_MERGE_TARGET", None)
            env.pop("avernet.prePush.mergeTarget", None)
            env.setdefault("HOME", str(developer))
            result = self._resolve(developer, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)

    def test_environment_override_takes_priority_over_git_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, developer = self._make_remote_and_repo(root)
            # Publish a second branch the override will target.
            self._add_branch(remote, developer, branch="release", path="src/baas/release.txt")
            override_target_sha = _git(developer, "rev-parse", "origin/release")
            # git config points at a no-op branch; env must win.
            _git(developer, "config", "avernet.prePush.mergeTarget", "origin/dev")

            env = os.environ.copy()
            env["AVERNET_PRE_PUSH_MERGE_TARGET"] = "origin/release"
            result = self._resolve(developer, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            expected = _git(developer, "merge-base", "HEAD", override_target_sha)
            self.assertEqual(result.stdout.strip(), expected)

    def test_git_config_used_when_environment_unset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, developer = self._make_remote_and_repo(root)
            self._add_branch(remote, developer, branch="release", path="src/baas/release.txt")
            release_sha = _git(developer, "rev-parse", "origin/release")
            _git(developer, "config", "avernet.prePush.mergeTarget", "origin/release")

            env = os.environ.copy()
            env.pop("AVERNET_PRE_PUSH_MERGE_TARGET", None)
            result = self._resolve(developer, env=env)

            self.assertEqual(result.returncode, 0, result.stderr)
            expected = _git(developer, "merge-base", "HEAD", release_sha)
            self.assertEqual(result.stdout.strip(), expected)

    def test_invalid_target_format_fails_loudly_without_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, developer = self._make_remote_and_repo(root)
            env = os.environ.copy()
            env["AVERNET_PRE_PUSH_MERGE_TARGET"] = "dev"  # missing <remote>/

            result = self._resolve(developer, env=env)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("<remote>/<branch>", result.stderr)
            # The gate must never receive a base ref for a broken target.
            self.assertEqual(result.stdout.strip(), "")

    def test_unreachable_target_fails_loudly_with_remediation_hint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, developer = self._make_remote_and_repo(root)
            env = os.environ.copy()
            env["AVERNET_PRE_PUSH_MERGE_TARGET"] = "origin/does-not-exist"

            result = self._resolve(developer, env=env)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed to fetch", result.stderr)
            self.assertIn("AVERNET_PRE_PUSH_MERGE_TARGET", result.stderr)
            self.assertEqual(result.stdout.strip(), "")

    def _add_branch(self, remote: Path, developer: Path, *, branch: str, path: str) -> None:
        # Publish the branch from a throwaway publisher clone so the developer
        # can fetch it (keeps the developer checkout history unchanged).
        publisher = remote.parent / f"publisher-{branch}"
        _run("git", "clone", "--branch", "dev", str(remote), str(publisher), check=True)
        _git(publisher, "config", "user.name", "CI Test")
        _git(publisher, "config", "user.email", "ci-test@example.com")
        _write(publisher, path, f"{branch} only\n")
        _git(publisher, "checkout", "-b", branch)
        _git(publisher, "add", ".")
        _git(publisher, "commit", "-m", f"advance {branch}")
        _git(publisher, "push", "origin", branch)
        _git(developer, "fetch", "origin", branch)


class CiTestCoverageGateWiringTest(unittest.TestCase):
    """Asserts each Python module's ci_test.sh auto-derives the changed-line
    coverage base ref when --base is omitted, so a local `./scripts/ci_test.sh`
    (or `just test-ci` for baas) enforces the same gate as GitHub CI instead of
    silently skipping it. The justfile `test` recipe is intentionally left on
    the fast run_ci_pipeline path (no gate); the gate lives in ci_test.sh."""

    def _ci_test_text(self, module: str) -> str:
        return CI_TEST[module].read_text(encoding="utf-8")

    def test_ci_test_auto_derives_base_when_omitted(self) -> None:
        for module in CI_TEST:
            with self.subTest(module=module):
                text = self._ci_test_text(module)
                # The auto-derive branch must source the shared helper so a
                # direct ./scripts/ci_test.sh and CI's explicit --base share one
                # resolution path.
                self.assertIn('if [[ -z "$base" ]]; then', text)
                self.assertIn("resolve_base_ref.sh", text)
                self.assertIn('base="$(resolve_base_ref)"', text)

    def test_ci_test_changed_line_thresholds_match_github_ci(self) -> None:
        # CI passes --base via unit-tests.yml (the workflow-diff test asserts
        # that wiring for all four modules); the threshold lives in each
        # ci_test.sh and must match what CI effectively enforces.
        for module in CI_TEST:
            with self.subTest(module=module):
                self.assertIn(
                    f"--min-change-line-coverage {CI_CHANGE_LINE_COVERAGE[module]}",
                    self._ci_test_text(module),
                )

    def test_ci_test_reports_failure_when_base_cannot_be_resolved(self) -> None:
        for module in CI_TEST:
            with self.subTest(module=module):
                self.assertIn(
                    "could not resolve changed-line coverage base ref",
                    self._ci_test_text(module),
                )

    def test_justfiles_keep_fast_run_ci_pipeline_path(self) -> None:
        # Approach A guard: the gate lives in ci_test.sh, not the justfile
        # `test` recipe. baas/gateway `just test` must stay on run_ci_pipeline
        # (fast dev path, no gate); re-introducing justfile delegation would be
        # a regression of this fix's scope decision.
        for justfile in (REPO_ROOT / "src/baas/justfile", REPO_ROOT / "src/gateway/justfile"):
            with self.subTest(justfile=str(justfile)):
                text = justfile.read_text(encoding="utf-8")
                self.assertIn("run_ci_pipeline", text)
                self.assertNotIn("resolve_base_ref", text)
                self.assertNotIn("ci_test.sh --base", text)


if __name__ == "__main__":
    unittest.main()
