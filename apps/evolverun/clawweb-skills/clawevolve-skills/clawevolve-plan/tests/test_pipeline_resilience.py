from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

PLAN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLAN_ROOT))

from clawevolve_plan.bench.case_contract import _fallback_contract  # noqa: E402
from clawevolve_plan.bench.template_builder import (  # noqa: E402
    _bounded_timeout_seconds,
    _unique_template_ids,
    assign_case_template_ids,
    render_templates,
)
from clawevolve_plan.spec.renderer import (  # noqa: E402
    render_goal_markdown,
    render_markdown,
)
from clawevolve_plan.integration.clawweb import (  # noqa: E402
    ClawWebClient,
    post_step_report,
)
from clawevolve_plan.pipeline.existing import (  # noqa: E402
    _existing_plan_result,
    _validate_cached_templates,
)
from clawevolve_plan.pipeline.runner import _handle_plan_failure  # noqa: E402


def _valid_task_template(template_id: str) -> str:
    sections = [
        "## Prompt",
        "## Expected Behavior",
        "## Grading Criteria",
        "## Automated Checks",
        "## LLM Judge Rubric",
        "## Workspace Files",
        "## Additional Notes",
    ]
    body = "\n\ncontent\n\n".join(sections)
    return f'---\nid: "{template_id}"\n---\n\n# Task Template\n\n{body}\n'


class FailureBoundaryTests(unittest.TestCase):
    def test_failure_report_error_does_not_replace_primary_error(self):
        def broken_reporter(*args, **kwargs):
            raise RuntimeError("report transport down")

        payload = _handle_plan_failure(
            ValueError("primary plan failure"),
            task_id="EV-1",
            step_id="STEP-1",
            output_dir=None,
            step_reporter=broken_reporter,
        )

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["error"], "ValueError: primary plan failure")
        self.assertIn("report transport down", payload["failure_report_error"])


class ExistingArtifactTests(unittest.TestCase):
    def test_partial_markdown_only_result_is_not_reused(self):
        with tempfile.TemporaryDirectory(prefix="plan-existing-partial-") as td:
            output = Path(td)
            (output / "objective.md").write_text("objective", encoding="utf-8")
            (output / "spec-v0.md").write_text("spec", encoding="utf-8")
            self.assertIsNone(_existing_plan_result(output))

    def test_complete_consistent_result_is_reused(self):
        with tempfile.TemporaryDirectory(prefix="plan-existing-complete-") as td:
            output = Path(td)
            objective = {}
            spec = {}
            (output / "objective.md").write_text(
                render_goal_markdown(objective), encoding="utf-8"
            )
            (output / "spec-v0.md").write_text(
                render_markdown(spec), encoding="utf-8"
            )
            plan = {
                "cases": [
                    {
                        "case_id": "case-1",
                        "source_session_id": "session-1",
                        "query": "one",
                    },
                    {
                        "case_id": "case-2",
                        "source_session_id": "session-2",
                        "query": "two",
                    },
                ]
            }
            _, _, _, manifest = render_templates(plan, output)
            manifest_by_case = {
                item["case_id"]: item for item in manifest["templates"]
            }
            for case in plan["cases"]:
                item = manifest_by_case[case["case_id"]]
                case["template_id"] = item["id"]
                case["split"] = item["split"]
            contracts = [
                _fallback_contract(case, "improve", {"intent_text": "improve"})
                for case in plan["cases"]
            ]
            for name, payload in {
                "objective.json": objective,
                "spec-v0.json": spec,
                "case_contracts.json": {"contracts": contracts},
                "case_contract_audit.json": {
                    "schema_version": "clawevolve.case-contract.v1",
                    "status": "validated",
                    "contract_count": 2,
                    "batches": [
                        {
                            "batch": 1,
                            "status": "validated",
                            "case_ids": ["case-1", "case-2"],
                        }
                    ],
                },
                "input_manifest.json": {"items": []},
                "clawweb_upload_result.json": {"status": "skipped"},
                "clawbench_manifest.json": manifest,
            }.items():
                (output / name).write_text(json.dumps(payload), encoding="utf-8")

            result = _existing_plan_result(output)

            self.assertIsNotNone(result)
            self.assertEqual(result["template_count"], 2)

    def test_tampered_cached_template_is_rejected_before_reuse(self):
        with tempfile.TemporaryDirectory(prefix="plan-existing-tampered-") as td:
            root = Path(td)
            template_dir = root / "templates"
            template_path = template_dir / "opt" / "task_one.md"
            template_path.parent.mkdir(parents=True)
            original = _valid_task_template("task_one")
            template_path.write_text(original, encoding="utf-8")
            aggregate_zip = root / "clawbench_dataset.zip"
            with zipfile.ZipFile(aggregate_zip, "w") as archive:
                archive.write(template_path, "opt/task_one.md")
            template_path.write_text(
                original.replace("## Prompt", "## Prompt Removed"),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Prompt"):
                _validate_cached_templates(
                    template_dir,
                    [
                        {
                            "id": "task_one",
                            "relative_path": "opt/task_one.md",
                            "split": "train",
                        }
                    ],
                    aggregate_zip,
                )


class TemplateContractTests(unittest.TestCase):
    def test_timeout_rejects_bool_non_numeric_and_out_of_range(self):
        for value in (True, "slow", 0, -1, 3601):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _bounded_timeout_seconds(value)
        self.assertEqual(_bounded_timeout_seconds("600"), 600)

    def test_duplicate_case_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate bench case identity"):
            _unique_template_ids(
                [
                    {"case_id": "same", "query": "a"},
                    {"case_id": "same", "query": "b"},
                ]
            )

    def test_assigned_template_id_is_reused_by_contract_and_renderer(self):
        cases = assign_case_template_ids(
            [{"case_id": "case-1", "query": "完成任务", "case_type": "bad"}]
        )
        template_id = cases[0]["template_id"]
        self.assertEqual(_unique_template_ids(cases), [template_id])

    def test_renderer_rejects_contract_template_id_drift(self):
        case = assign_case_template_ids(
            [{"case_id": "case-1", "query": "完成任务", "case_type": "bad"}]
        )[0]
        contract = {
            "case_id": "case-1",
            "template_id": "wrong-template-id",
            "task_contract": {},
            "grading_strategy": {},
            "automated_checks": [],
            "replayability": {"replayable": True},
        }
        with tempfile.TemporaryDirectory(prefix="plan-template-drift-") as td:
            with self.assertRaisesRegex(ValueError, "template_id does not match"):
                render_templates(
                    {"cases": [case]},
                    Path(td),
                    contracts={"case-1": contract},
                )

    def test_llm_judge_template_has_no_python_automated_check(self):
        case = assign_case_template_ids(
            [{"case_id": "case-1", "query": "开放问题", "case_type": "prospective"}]
        )[0]
        contract = _fallback_contract(case, "完成任务", {"intent_text": "完成任务"})

        with tempfile.TemporaryDirectory(prefix="plan-template-llm-judge-") as td:
            templates_dir, _, _, manifest = render_templates(
                {"cases": [case]},
                Path(td),
                contracts={"case-1": contract},
            )
            template = (
                templates_dir / manifest["templates"][0]["relative_path"]
            ).read_text(encoding="utf-8")

        self.assertIn('grading_type: "llm_judge"', template)
        self.assertIn("## Automated Checks", template)
        self.assertIn("No deterministic automated checks.", template)
        self.assertNotIn("def grade(", template)
        self.assertNotIn("transcript_present", template)

    def test_hybrid_template_keeps_python_for_valid_checks(self):
        case = assign_case_template_ids(
            [{"case_id": "case-1", "query": "执行任务", "case_type": "bad"}]
        )[0]
        contract = _fallback_contract(case, "完成任务", {"intent_text": "完成任务"})
        contract["grading_strategy"]["grading_type"] = "hybrid"
        contract["automated_checks"] = [{"type": "transcript_present"}]

        with tempfile.TemporaryDirectory(prefix="plan-template-hybrid-") as td:
            templates_dir, _, _, manifest = render_templates(
                {"cases": [case]},
                Path(td),
                contracts={"case-1": contract},
            )
            template = (
                templates_dir / manifest["templates"][0]["relative_path"]
            ).read_text(encoding="utf-8")

        self.assertIn('grading_type: "hybrid"', template)
        self.assertIn("def grade(", template)
        self.assertIn("transcript_present", template)

    def test_slug_collision_gets_stable_unique_suffix(self):
        ids = _unique_template_ids(
            [
                {"case_id": "task_A", "query": "a"},
                {"case_id": "task-a", "query": "b"},
            ]
        )
        self.assertEqual(len(ids), len(set(ids)))


class HttpRetryTests(unittest.TestCase):
    @staticmethod
    def _http_error(code: int) -> urllib.error.HTTPError:
        return urllib.error.HTTPError(
            "https://example.invalid",
            code,
            "error",
            {},
            io.BytesIO(b'{"error":"bad request"}'),
        )

    def test_step_report_does_not_retry_non_retryable_4xx(self):
        class Opener:
            calls = 0

            def open(self, *args, **kwargs):
                self.calls += 1
                raise HttpRetryTests._http_error(401)

        opener = Opener()
        with (
            patch(
                "clawevolve_plan.integration.clawweb._report_opener",
                return_value=opener,
            ),
            patch("clawevolve_plan.integration.clawweb.time.sleep") as sleep,
        ):
            result = post_step_report(
                "EV-1", "STEP-1", status="failed", summary="failed"
            )
        self.assertEqual(opener.calls, 1)
        self.assertEqual(result["attempts"], 1)
        sleep.assert_not_called()

    def test_create_domain_conflict_is_not_retried(self):
        client = ClawWebClient(base_url="https://example.invalid")
        client.opener.open = unittest.mock.Mock(side_effect=self._http_error(409))
        with patch("clawevolve_plan.integration.clawweb.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "HTTP 409"):
                client.create_domain("existing-domain", "description")
        self.assertEqual(client.opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_client_does_not_retry_non_retryable_4xx(self):
        client = ClawWebClient(base_url="https://example.invalid")
        client.opener.open = unittest.mock.Mock(side_effect=self._http_error(404))
        request = unittest.mock.Mock(data=None)
        with patch("clawevolve_plan.integration.clawweb.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                client._open_with_retry(request, "/x", "GET")
        self.assertEqual(client.opener.open.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class StepReportMetricTests(unittest.TestCase):
    def test_missing_primary_metric_is_rejected(self):
        from clawevolve_plan.pipeline.step_report_payload import (
            build_step_report_output,
        )

        with self.assertRaisesRegex(ValueError, "primary_metric is required"):
            build_step_report_output(
                {"acceptance_criteria": {"task_success_rate_min": "NaN"}},
                {"goal": {"goal_text": "improve"}},
                {"template_count": 0, "templates": []},
                {},
            )


class PlanInputSelectionTests(unittest.TestCase):
    def test_multiple_plan_sources_are_rejected_instead_of_mtime_selection(self):
        from clawevolve_plan.io import find_plan_source

        with tempfile.TemporaryDirectory(prefix="plan-source-ambiguous-") as td:
            root = Path(td)
            (root / "a").mkdir()
            (root / "b").mkdir()
            (root / "a" / "plan-source.json").write_text("{}", encoding="utf-8")
            (root / "b" / "plan-source.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Multiple Diagnose Plan Sources"):
                find_plan_source(root)
