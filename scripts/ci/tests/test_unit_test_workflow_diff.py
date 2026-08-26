from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/unit-tests.yml"
SINGLEBOX_WORKFLOW_PATH = REPO_ROOT / ".github/workflows/singlebox-coverage.yml"
E2E_WORKFLOW_PATH = REPO_ROOT / ".github/workflows/e2e-tests.yml"
SPEC_MARKDOWN_EXCLUDE_PATHSPEC = ":(exclude)src/*/specs/**/*.md"
SPEC_MARKDOWN_EXCLUDE = f"'{SPEC_MARKDOWN_EXCLUDE_PATHSPEC}'"
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
# Full pathspec list each change detector passes to "git diff --name-only",
# including the module the job also runs for and the SDD spec markdown exclusion.
DETECTOR_PATHSPECS = {
    "BCS_BASE_REF": ["src/bcs"],
    "BACKEND_BASE_REF": [
        "src/backend",
        "scripts/ci/legacy_skill_compatibility.sh",
        ".github/workflows/unit-tests.yml",
    ],
    "ENGINE_BASE_REF": ["src/engine"],
    "BAAS_BASE_REF": ["src/baas"],
    "GATEWAY_BASE_REF": ["src/gateway"],
    "PROXY_BASE_REF": ["src/proxy"],
}
BACKEND_PATHSPECS = [
    *DETECTOR_PATHSPECS["BACKEND_BASE_REF"],
    SPEC_MARKDOWN_EXCLUDE_PATHSPEC,
]


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
                "src/gateway",
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

    def test_spec_markdown_never_gates_module_tests(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        for env_name, pathspecs in DETECTOR_PATHSPECS.items():
            self.assertIn(
                f'git diff --name-only "${{{env_name}}}" -- '
                f'{" ".join(pathspecs)} {SPEC_MARKDOWN_EXCLUDE} || true)',
                workflow,
            )
        singlebox = SINGLEBOX_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn(f"            {SPEC_MARKDOWN_EXCLUDE} \\\n", singlebox)
        e2e = E2E_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn(
            'git diff --name-only "${BCS_BASE_REF}" -- src/bcs '
            f"{SPEC_MARKDOWN_EXCLUDE} || true)",
            e2e,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = Path(temporary_directory) / "repository"
            repository.mkdir()
            _git(repository, "init", "--initial-branch=target")
            _git(repository, "config", "user.name", "CI Test")
            _git(repository, "config", "user.email", "ci-test@example.com")

            _write(repository, "src/backend/src/service.py", "VALUE = 1\n")
            _write(repository, "src/backend/specs/feature/spec.md", "# spec\n")
            _write(
                repository, "src/backend/specs/feature/assemble.sh", "echo baseline\n"
            )
            _git(repository, "add", ".")
            _git(repository, "commit", "-m", "baseline")

            def detected(branch: str, changes: dict[str, str]) -> list[str]:
                """Merge a PR branch with *changes*; return what the detector sees."""
                _git(repository, "switch", "--create", branch, "target")
                for relative_path, content in changes.items():
                    _write(repository, relative_path, content)
                _git(repository, "add", ".")
                _git(repository, "commit", "-m", branch)
                _git(repository, "switch", "target")
                _git(repository, "merge", "--no-ff", branch, "-m", f"merge {branch}")
                # Mirrors the PR-run detector: base is the merge commit's first parent.
                output = _git(
                    repository,
                    "diff",
                    "--name-only",
                    "HEAD^1",
                    "--",
                    *BACKEND_PATHSPECS,
                )
                return output.splitlines()

            self.assertEqual(
                detected(
                    "docs-only",
                    {
                        "src/backend/specs/feature/spec.md": "# spec (revised)\n",
                        "src/backend/specs/feature/plan.md": "# plan\n",
                        "src/backend/specs/feature/nested/notes.md": "# notes\n",
                    },
                ),
                [],
            )
            self.assertEqual(
                detected("code-only", {"src/backend/src/service.py": "VALUE = 2\n"}),
                ["src/backend/src/service.py"],
            )
            # Executable and data files under specs/ are not documentation.
            self.assertEqual(
                detected(
                    "spec-script",
                    {"src/backend/specs/feature/assemble.sh": "echo changed\n"},
                ),
                ["src/backend/specs/feature/assemble.sh"],
            )
            self.assertEqual(
                detected(
                    "docs-and-code",
                    {
                        "src/backend/specs/feature/spec.md": "# spec (again)\n",
                        "src/backend/src/service.py": "VALUE = 3\n",
                    },
                ),
                ["src/backend/src/service.py"],
            )


if __name__ == "__main__":
    unittest.main()
