from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/unit-tests.yml"
BACKEND_WORKFLOW_PATH = REPO_ROOT / ".github/workflows/backend-unit-tests.yml"
BACKEND_DIFF_PATHS = (
    "apps/backend",
    "singlebox/ci/legacy_skill_compatibility.sh",
    ".github/workflows/backend-unit-tests.yml",
)
BACKEND_CI_PATH = REPO_ROOT / "apps/backend/scripts/ci_test.sh"
PR_MERGE_PARENT_EXPRESSION = (
    "${{ github.event_name == 'pull_request' && 'HEAD^1' || 'origin/dev' }}"
)
MODULE_JOBS = {
    "bcs": ("BCS_BASE_REF", "apps/bcs"),
    "backend": ("BACKEND_BASE_REF", "apps/backend"),
    "engine": ("ENGINE_BASE_REF", "engine/adapter"),
    "baas": ("BAAS_BASE_REF", "apps/baas"),
    "gateway": ("GATEWAY_BASE_REF", "apps/gateway"),
}


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _write(repository: Path, relative_path: str, content: str) -> None:
    path = repository / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _backend_pathspec(workflow: str) -> tuple[str, ...]:
    command = next(
        line
        for line in workflow.splitlines()
        if "git diff --name-only" in line and "mapfile -t files" in line
    )
    return tuple(command.split(" -- ", 1)[1].split(" || true", 1)[0].split())


class UnitTestWorkflowDiffTest(unittest.TestCase):
    def test_backend_worker_crashes_fail_fast(self) -> None:
        backend_job = BACKEND_WORKFLOW_PATH.read_text(encoding="utf-8")
        backend_ci = BACKEND_CI_PATH.read_text(encoding="utf-8")

        self.assertIn("    timeout-minutes: 30\n", backend_job)
        self.assertIn("--max-worker-restart=0", backend_ci)

    def test_backend_test_failures_keep_diagnostics_before_job_timeout(self) -> None:
        # The dedicated workflow is read here on purpose: it must exist for the
        # diagnostics contract to hold, and the timeout assertion itself lives in
        # the worker-crash test.
        BACKEND_WORKFLOW_PATH.read_text(encoding="utf-8")
        backend_ci = BACKEND_CI_PATH.read_text(encoding="utf-8")

        self.assertIn("--maxfail=1", backend_ci)
        self.assertIn("--durations=30", backend_ci)
        self.assertIn("faulthandler_timeout=120", backend_ci)

    def test_backend_pathspec_ignores_bcs_workflow_but_catches_backend_inputs(self) -> None:
        workflow = BACKEND_WORKFLOW_PATH.read_text(encoding="utf-8")
        backend_paths = _backend_pathspec(workflow)
        self.assertEqual(backend_paths, BACKEND_DIFF_PATHS)

        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            repository.mkdir()
            _git(repository, "init", "--initial-branch=target")
            _git(repository, "config", "user.name", "CI Test")
            _git(repository, "config", "user.email", "ci-test@example.com")
            _write(repository, "apps/bcs/baseline.txt", "baseline\n")
            _write(repository, ".github/workflows/unit-tests.yml", "bcs: baseline\n")
            for path in backend_paths:
                _write(
                    repository,
                    f"{path}/baseline.txt" if path == "apps/backend" else path,
                    "baseline\n",
                )
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "baseline")

            _write(repository, ".github/workflows/unit-tests.yml", "bcs: changed\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "BCS CI only")
            self.assertEqual(
                _git(repository, "diff", "--name-only", "HEAD^1"),
                ".github/workflows/unit-tests.yml",
            )
            self.assertEqual(
                _git(
                    repository,
                    "diff",
                    "--name-only",
                    "HEAD^1",
                    "--",
                    *backend_paths,
                ),
                "",
            )

            for path in backend_paths:
                with self.subTest(path=path):
                    changed_path = (
                        f"{path}/baseline.txt" if path == "apps/backend" else path
                    )
                    _write(repository, changed_path, f"{path} changed\n")
                    _git(repository, "add", ".")
                    _git(repository, "commit", "-m", f"change {path}")
                    self.assertEqual(
                        _git(
                            repository,
                            "diff",
                            "--name-only",
                            "HEAD^1",
                            "--",
                            *backend_paths,
                        ),
                        changed_path,
                    )

            _write(
                repository, ".github/workflows/backend-unit-tests.yml", "backend: changed\n"
            )
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "Backend CI")
            self.assertEqual(
                _git(repository, "diff", "--name-only", "HEAD^1", "--", *backend_paths),
                ".github/workflows/backend-unit-tests.yml",
            )

    def test_backend_workflow_owns_only_backend_change_detection(self) -> None:
        workflow = BACKEND_WORKFLOW_PATH.read_text(encoding="utf-8")
        shared = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("name: Backend unit tests", workflow)
        self.assertIn("\n  backend:\n    name: Backend unit tests\n", workflow)
        self.assertIn(".github/workflows/backend-unit-tests.yml", workflow)
        self.assertIn("apps/backend singlebox/ci/legacy_skill_compatibility.sh", workflow)
        self.assertNotIn(".github/workflows/unit-tests.yml || true", workflow)
        self.assertNotIn("\n  backend:\n", shared)
        self.assertIn(
            "group: backend-unit-tests-${{ github.workflow }}-${{ github.ref }}", workflow
        )
        self.assertIn("group: unit-tests-${{ github.workflow }}-${{ github.ref }}", shared)

    def test_pr_module_diffs_use_the_checked_out_merge_parent(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        for job_name, (env_name, module_path) in MODULE_JOBS.items():
            if job_name == "bcs":
                self.assertIn("run: python3 apps/bcs/scripts/ci/detect_changes.py", workflow)
                self.assertIn("BCS_BASE_REF: ${{ steps.changes.outputs.base-ref }}", workflow)
                continue
            module_workflow = (
                BACKEND_WORKFLOW_PATH.read_text(encoding="utf-8")
                if job_name == "backend"
                else workflow
            )
            assignment = f"{env_name}: {PR_MERGE_PARENT_EXPRESSION}"
            self.assertEqual(module_workflow.count(assignment), 1)
            self.assertIn(
                f'git diff --name-only "${{{env_name}}}" -- {module_path}',
                module_workflow,
            )
        for coverage_argument in (
            '--base-ref "$BCS_BASE_REF"',
            '--base "$ENGINE_BASE_REF"',
            '--base "${BAAS_BASE_REF}"',
            '--base "${GATEWAY_BASE_REF}"',
        ):
            self.assertIn(coverage_argument, workflow)
        self.assertIn(
            '--base "$BACKEND_BASE_REF"',
            BACKEND_WORKFLOW_PATH.read_text(encoding="utf-8"),
        )
        self.assertIn(
            "      - name: Validate unit-test diff behavior\n"
            "        working-directory: ${{ github.workspace }}\n"
            "        run: python3 singlebox/ci/tests/test_unit_test_workflow_diff.py\n",
            workflow,
        )
        self.assertIn("steps.changes.outputs.dsh-bcn-changed == 'true'", workflow)
        self.assertIn("npm run ci", workflow)

        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            repository.mkdir()
            _git(repository, "init", "--initial-branch=target")
            _git(repository, "config", "user.name", "CI Test")
            _git(repository, "config", "user.email", "ci-test@example.com")

            for _, module_path in MODULE_JOBS.values():
                _write(repository, f"{module_path}/baseline.txt", "baseline\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "baseline")
            _git(repository, "branch", "pull-request")

            for module_path in (
                "apps/backend",
                "engine/adapter",
                "apps/baas",
                "apps/gateway",
            ):
                _write(repository, f"{module_path}/target-only.txt", "target branch\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "advance target branch")

            _git(repository, "switch", "pull-request")
            _write(repository, "apps/bcs/pull-request.txt", "pull request\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "change only BCS")

            _git(repository, "switch", "target")
            _git(
                repository,
                "merge",
                "--no-ff",
                "pull-request",
                "-m",
                "merge pull request",
            )

            changed_by_module = {
                job_name: _git(
                    repository, "diff", "--name-only", "HEAD^1", "--", module_path
                )
                for job_name, (_, module_path) in MODULE_JOBS.items()
            }
            self.assertEqual(changed_by_module["bcs"], "apps/bcs/pull-request.txt")
            self.assertEqual(changed_by_module["backend"], "")
            self.assertEqual(changed_by_module["engine"], "")
            self.assertEqual(changed_by_module["baas"], "")
            self.assertEqual(changed_by_module["gateway"], "")


if __name__ == "__main__":
    unittest.main()
