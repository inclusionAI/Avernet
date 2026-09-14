from __future__ import annotations

import sys
import unittest
from pathlib import Path


DIAGNOSE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIAGNOSE_ROOT))

from clawevolve_diagnose.integration.clawweb_events import (  # noqa: E402
    _build_step_report_payload,
)
from clawevolve_diagnose.run.step_reports import post_failure_report  # noqa: E402


class DiagnoseStepReportTests(unittest.TestCase):
    def test_failure_report_uses_clawweb_structured_error_contract(self):
        reports: list[dict] = []

        def reporter(task_id, step_id, **payload):
            reports.append({"task_id": task_id, "step_id": step_id, **payload})
            return {"status": "posted"}

        post_failure_report(
            reporter,
            task_id="EV-1",
            step_id="STEP-1",
            summary="Diagnose运行失败",
            error="ValueError: invalid case contract",
        )

        self.assertEqual(reports[-1]["error"], {
            "code": "DIAGNOSE_PIPELINE_FAILED",
            "message": "ValueError: invalid case contract",
            "retryable": True,
        })

    def test_transport_preserves_structured_error_object(self):
        error = {
            "code": "DIAGNOSE_PIPELINE_FAILED",
            "message": "ValueError: invalid case contract",
            "retryable": True,
        }
        payload = _build_step_report_payload(
            status="failed",
            summary="Diagnose运行失败",
            output=None,
            progress=None,
            error=error,
        )

        self.assertEqual(payload["error"], error)

    def test_failure_report_accepts_specific_error_code(self):
        reports: list[dict] = []

        def reporter(task_id, step_id, **payload):
            reports.append({"task_id": task_id, "step_id": step_id, **payload})
            return {"status": "posted"}

        post_failure_report(
            reporter,
            task_id="EV-1",
            step_id="STEP-1",
            summary="Diagnose运行失败：Judge执行失败",
            error="scope upgrade pending approval",
            error_code="DIAGNOSE_JUDGE_EXECUTION_FAILED",
        )

        self.assertEqual(
            reports[-1]["error"]["code"], "DIAGNOSE_JUDGE_EXECUTION_FAILED"
        )


if __name__ == "__main__":
    unittest.main()
