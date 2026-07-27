from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/unit-tests.yml"
PR_MERGE_PARENT_EXPRESSION = (
    "${{ github.event_name == 'pull_request' && 'HEAD^1' || 'origin/dev' }}"
)
MODULE_JOBS = {
    "bcs": ("BCS_BASE_REF", "src/bcs"),
    "backend": ("BACKEND_BASE_REF", "src/backend"),
    "engine": ("ENGINE_BASE_REF", "src/engine"),
    "baas": ("BAAS_BASE_REF", "src/baas"),
    "gateway": ("GATEWAY_BASE_REF", "src/gateway"),
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


class UnitTestWorkflowDiffTest(unittest.TestCase):
    def test_pr_module_diffs_use_the_checked_out_merge_parent(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        for env_name, module_path in MODULE_JOBS.values():
            assignment = f"{env_name}: {PR_MERGE_PARENT_EXPRESSION}"
            self.assertEqual(workflow.count(assignment), 1)
            self.assertIn(
                f'git diff --name-only "${{{env_name}}}" -- {module_path}',
                workflow,
            )
        for coverage_argument in (
            '--base-ref "$BCS_BASE_REF"',
            '--base "$BACKEND_BASE_REF"',
            '--base "$ENGINE_BASE_REF"',
            '--base "${BAAS_BASE_REF}"',
            '--base "${GATEWAY_BASE_REF}"',
        ):
            self.assertIn(coverage_argument, workflow)
        self.assertIn(
            "      - name: Validate unit-test diff behavior\n"
            "        working-directory: ${{ github.workspace }}\n"
            "        run: python3 scripts/ci/tests/test_unit_test_workflow_diff.py\n",
            workflow,
        )

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
                "src/backend",
                "src/engine",
                "src/baas",
            ):
                _write(repository, f"{module_path}/target-only.txt", "target branch\n")
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "advance target branch")

            _git(repository, "switch", "pull-request")
            _write(repository, "src/bcs/pull-request.txt", "pull request\n")
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
            self.assertEqual(changed_by_module["bcs"], "src/bcs/pull-request.txt")
            self.assertEqual(changed_by_module["backend"], "")
            self.assertEqual(changed_by_module["engine"], "")
            self.assertEqual(changed_by_module["baas"], "")
            self.assertEqual(changed_by_module["gateway"], "")

    def test_ci_test_sh_supports_resolve_base_flag(self) -> None:
        """Verify that all Python module ci_test.sh scripts accept --resolve-base."""
        for module_path in ("src/backend", "src/engine", "src/baas", "src/gateway"):
            script = REPO_ROOT / module_path / "scripts/ci_test.sh"
            with self.subTest(module=module_path):
                self.assertTrue(script.exists(), f"{script} missing")
                text = script.read_text(encoding="utf-8")
                self.assertIn("--resolve-base", text)

    def test_resolve_base_ref_script_exists_and_runs(self) -> None:
        """Verify the shared baseline resolution script is present and runnable."""
        script = REPO_ROOT / "scripts/ci/resolve_base_ref.sh"
        self.assertTrue(script.exists(), f"{script} missing")
        result = subprocess.run(
            ["bash", str(script)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"script failed: {result.stderr}")
        base_ref = result.stdout.strip()
        self.assertTrue(len(base_ref) > 0, "script produced empty output")


if __name__ == "__main__":
    unittest.main()
