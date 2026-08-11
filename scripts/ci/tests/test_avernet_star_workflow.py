from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github/workflows/avernet-star-daily.yml"
PROMPT = REPO_ROOT / ".github/codex/avernet-star-visual-qa.md"
SCHEMA = REPO_ROOT / ".github/codex/avernet-star-visual-qa.schema.json"
RENDER_PATCH = REPO_ROOT / "scripts/ci/avernet_star_growth.patch"
VENDOR = REPO_ROOT / "scripts/ci/vendor/avernet-star-daily"
STATS = VENDOR / "avernet_star_stats.py"
RENDERER = VENDOR / "generate_star_image.py"
UPSTREAM = VENDOR / "UPSTREAM.md"
STATS_SHA256 = "b2f53373ffb08efcb8f87db7262ceb941e97c187da8b2c6f099bc28f0b3ca283"
RENDERER_SHA256 = "333a1801475d79b21011beb305f3ea9842721242291860a7f71ddff341412f74"


class AvernetStarWorkflowTest(unittest.TestCase):
    def test_workflow_keeps_secrets_and_write_permission_narrow(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn('    - cron: "0 10 * * *"', workflow)
        self.assertIn("GITHUB_PAT_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("AVERNET_RD_ROSTER_JSON: ${{ secrets.AVERNET_RD_ROSTER_JSON }}", workflow)
        self.assertEqual(workflow.count("contents: write"), 1)
        self.assertEqual(workflow.count("openai-api-key: ${{ secrets.OPENAI_API_KEY }}"), 1)
        self.assertNotIn("OPENAI_API_KEY:", workflow)
        self.assertNotIn(" gh ", workflow)
        self.assertEqual(
            workflow.count("github.event_name != 'pull_request' && github.ref == 'refs/heads/dev'"),
            3,
        )
        self.assertIn("github.event_name == 'pull_request' && github.ref || 'publish'", workflow)

    def test_workflow_pins_skill_and_runs_codex_read_only_with_image(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("52c9e6d34efd40e6d808a1c086d3c75b5004e687", workflow)
        self.assertNotIn("repository: carolynli/efficiency", workflow)
        self.assertEqual(
            workflow.count('grep -F "$SKILL_REF" scripts/ci/vendor/avernet-star-daily/UPSTREAM.md'),
            2,
        )
        self.assertIn(STATS_SHA256, workflow)
        self.assertIn(RENDERER_SHA256, workflow)
        self.assertEqual(
            workflow.count("git apply --check scripts/ci/avernet_star_growth.patch"),
            2,
        )
        self.assertEqual(
            workflow.count("git apply --reverse --check scripts/ci/avernet_star_growth.patch"),
            2,
        )
        self.assertEqual(
            workflow.count("git apply --reverse scripts/ci/avernet_star_growth.patch"),
            1,
        )
        self.assertIn(
            "uses: openai/codex-action@52fe01ec70a42f454c9d2ebd47598f9fd6893d56 # v1.11",
            workflow,
        )
        self.assertIn('codex-version: "0.147.0"', workflow)
        self.assertIn('            ["--image",', workflow)
        self.assertIn("output-schema-file: .github/codex/avernet-star-visual-qa.schema.json", workflow)
        self.assertIn('"--ephemeral"]', workflow)
        self.assertNotIn("--ignore-user-config", workflow)
        self.assertIn("safety-strategy: drop-sudo", workflow)
        self.assertIn('permission-profile: ":read-only"', workflow)
        self.assertNotIn("sandbox:", workflow)
        self.assertIn(
            "trap 'rm -f \"$RUNNER_TEMP/avernet_star_rd_team.json\" \"$RUNNER_TEMP/avernet_star_row.json\"' EXIT",
            workflow,
        )
        visual_job = workflow.split("  visual_qa:\n", 1)[1].split("\n  publish:\n", 1)[0]
        self.assertEqual(visual_job.rsplit("      - name:", 1)[1].splitlines()[0], " Run Codex visual QA")

    def test_vendored_skill_matches_pinned_snapshot(self):
        self.assertEqual(hashlib.sha256(STATS.read_bytes()).hexdigest(), STATS_SHA256)
        self.assertEqual(hashlib.sha256(RENDERER.read_bytes()).hexdigest(), RENDERER_SHA256)
        self.assertIn(
            "52c9e6d34efd40e6d808a1c086d3c75b5004e687",
            UPSTREAM.read_text(encoding="utf-8"),
        )

    def test_visual_qa_schema_is_strict(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

        self.assertEqual(schema["required"], ["status", "summary", "issues"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["status"]["enum"], ["pass", "fail"])
        self.assertIn("Internal", PROMPT.read_text(encoding="utf-8"))
        self.assertIn("External", PROMPT.read_text(encoding="utf-8"))

    def test_render_patch_thins_dates_and_preserves_all_value_annotations(self):
        patch = RENDER_PATCH.read_text(encoding="utf-8")

        self.assertIn("scripts/ci/vendor/avernet-star-daily/generate_star_image.py", patch)
        self.assertIn("max_date_labels = 10", patch)
        self.assertIn("str(value)", patch)
        self.assertNotIn("-    for index, value in enumerate(totals):", patch)
        self.assertNotIn("totals[::", patch)


if __name__ == "__main__":
    unittest.main()
