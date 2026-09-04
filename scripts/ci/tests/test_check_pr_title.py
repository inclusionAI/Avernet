from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from scripts.ci.check_pr_title import is_valid_pr_title


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/pr-title.yml"
CHECKER = REPO_ROOT / "scripts/ci/check_pr_title.py"


class PullRequestTitleTest(unittest.TestCase):
    def test_accepts_repository_title_convention(self) -> None:
        valid_titles = (
            "feat: add whitelist observed state",
            "fix: 修复沙箱环境变量配置丢失问题",
            "feat(backend): add whitelist observed state",
            "fix(aliyun_ack): 修复沙箱环境变量配置丢失问题",
            "docs(bot-config-manifest): add Chinese user manual",
            "feat(baas/bot_runtime): expose runtime status",
            "feat(bcs,gateway): expose OpenAPI auth schema",
            "fix(BCS): keep a legacy uppercase scope during transition",
            "feat(bcs, gateway): coordinate multiple modules",
            "build(deps-dev): bump test dependencies",
        )

        for title in valid_titles:
            with self.subTest(title=title):
                self.assertTrue(is_valid_pr_title(title))

    def test_rejects_non_conforming_titles(self) -> None:
        invalid_titles = (
            "Dev haoqian 20260903",
            "fix openapi iam-token aliyun model",
            "perf(bcs): unsupported type",
            "Feat(bcs): uppercase type",
            "feat(): empty scope",
            "feat( ): blank scope",
            "feat(bcs: unclosed scope",
            "fix(bcs):",
            "fix(bcs):  extra space",
            "fix(bcs): trailing space ",
        )

        for title in invalid_titles:
            with self.subTest(title=title):
                self.assertFalse(is_valid_pr_title(title))

    def test_cli_reports_actionable_failure(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CHECKER), "--title", "Dev haoqian 20260903"],
            cwd=REPO_ROOT,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("Expected: <type>: <concise outcome>", result.stdout)
        self.assertIn("or: <type>(<scope>): <concise outcome>", result.stdout)
        self.assertIn(
            "feat | fix | refactor | docs | test | ci | build | chore",
            result.stdout,
        )

    def test_workflow_runs_for_every_pr_title_change(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("  pull_request:\n", workflow)
        self.assertIn("      - edited\n", workflow)
        self.assertNotIn("    paths:", workflow)
        self.assertIn("PR_TITLE: ${{ github.event.pull_request.title }}", workflow)
        self.assertIn("python3 scripts/ci/check_pr_title.py", workflow)


if __name__ == "__main__":
    unittest.main()
