from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clawevolve_diagnose.cli import build_parser
from clawevolve_diagnose.models import RunResult
from clawevolve_diagnose.run import command as command_module


def _args(tmp_path: Path) -> Any:
    return build_parser().parse_args(
        [
            "--task-id",
            "TASK-1",
            "--step-id",
            "STEP-1",
            "--api-key",
            "test-key",
            "--output-dir",
            str(tmp_path),
            "--intent",
            "诊断测试",
        ]
    )


def _successful_result(tmp_path: Path) -> RunResult:
    summary_path = tmp_path / "summary.json"
    summary = {
        "task_id": "TASK-1",
        "step_id": "STEP-1",
        "source": "local",
        "run_dir": str(tmp_path),
        "artifacts": {},
        "selection_report": {},
        "warnings": [],
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    return RunResult(summary_path=summary_path, summary=summary)


def _all_judges_failed_result(tmp_path: Path) -> RunResult:
    result = _successful_result(tmp_path)
    result.summary["selection_report"] = {
        "judge_lookup": {
            "all_judge_assessments_failed": True,
            "judged_session_count": 1,
            "failed_session_count": 1,
            "judge_backend": "subagent",
            "judge_stop_reason": "analysis_session_limit_reached",
            "judge_failure_samples": [
                "scope upgrade pending approval (requestId: request-123)"
            ],
        }
    }
    return result


def test_successful_command_reports_only_final_event(
    monkeypatch: Any, tmp_path: Path
) -> None:
    reports: list[dict[str, Any]] = []

    def reporter(task_id: str, step_id: str, **payload: Any) -> dict[str, Any]:
        reports.append({"task_id": task_id, "step_id": step_id, **payload})
        return {"status": "posted", "reported_status": payload["status"]}

    monkeypatch.setattr(
        command_module, "run_pipeline", lambda request: _successful_result(tmp_path)
    )

    result = command_module.run_diagnose_command(
        _args(tmp_path), step_reporter=reporter
    )

    assert result.exit_code == 0
    assert [report["status"] for report in reports] == ["succeeded"]
    assert "progress" not in reports[0]
    assert set(result.payload["clawweb_upload"]) == {"final"}


def test_failed_command_reports_only_final_event(
    monkeypatch: Any, tmp_path: Path
) -> None:
    reports: list[dict[str, Any]] = []

    def reporter(task_id: str, step_id: str, **payload: Any) -> dict[str, Any]:
        reports.append({"task_id": task_id, "step_id": step_id, **payload})
        return {"status": "posted", "reported_status": payload["status"]}

    def fail_pipeline(request: Any) -> RunResult:
        raise RuntimeError("diagnose failed")

    monkeypatch.setattr(command_module, "run_pipeline", fail_pipeline)

    result = command_module.run_diagnose_command(
        _args(tmp_path), step_reporter=reporter
    )

    assert result.exit_code == 1
    assert [report["status"] for report in reports] == ["failed"]
    assert "progress" not in reports[0]
    assert set(result.payload["clawweb_upload"]) == {"final"}


def test_all_judges_failed_reports_failure_instead_of_success(
    monkeypatch: Any, tmp_path: Path
) -> None:
    reports: list[dict[str, Any]] = []

    def reporter(task_id: str, step_id: str, **payload: Any) -> dict[str, Any]:
        reports.append({"task_id": task_id, "step_id": step_id, **payload})
        return {"status": "posted", "reported_status": payload["status"]}

    monkeypatch.setattr(
        command_module,
        "run_pipeline",
        lambda request: _all_judges_failed_result(tmp_path),
    )

    result = command_module.run_diagnose_command(
        _args(tmp_path), step_reporter=reporter
    )

    assert result.exit_code == 1
    assert [report["status"] for report in reports] == ["failed"]
    assert reports[0]["summary"] == "Diagnose运行失败：Judge执行失败"
    assert reports[0]["error"]["code"] == "DIAGNOSE_JUDGE_EXECUTION_FAILED"
    assert "scope upgrade pending approval" in reports[0]["error"]["message"]
    assert result.payload["status"] == "error"
