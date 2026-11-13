"""Shared AIS task-package adapter for the ClawInsight batch runtime."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from clawweb_batch.bootstrap import main as batch_main


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _config_path(task: dict[str, Any], input_dir: Path) -> Path:
    value = task.get("input", {}).get("batchConfigPath", "config.json")
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("input.batchConfigPath must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        path = input_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError("independent ClawInsight config was not found")
    return path


def _resolved_config(source: Path, task_output: Path) -> Path:
    config = _object(source)
    if not isinstance(config.get("llm_config_file"), str):
        raise ValueError("batch config must provide llm_config_file")
    config["output_dir"] = str((task_output / "analysis").resolve())
    config["state_dir"] = str((task_output / "state").resolve())
    llm_path = Path(config["llm_config_file"])
    config["llm_config_file"] = str((llm_path if llm_path.is_absolute() else source.parent / llm_path).resolve())
    resolved = task_output / "resolved-clawinsight-config.json"
    resolved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    resolved.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    resolved.chmod(0o600)
    return resolved


def _latest_report(output_dir: Path) -> tuple[Path | None, Path | None, Path | None]:
    runs = [path for path in output_dir.glob("analysis/*") if path.is_dir()]
    if not runs:
        return None, None, None
    run = sorted(runs, key=lambda path: path.name)[-1]
    return run / "run.json", run / "review.md", run / "requests.json"


def _write_result(path: Path, task_id: str, success: bool, summary: str,
                  report_paths: tuple[Path | None, Path | None, Path | None]) -> None:
    artifacts: dict[str, dict[str, str]] = {}
    for name, content_type, artifact in zip(
        ("run", "review", "requests"),
        ("application/json", "text/markdown", "application/json"),
        report_paths,
    ):
        if artifact is not None and artifact.is_file():
            artifacts[name] = {"localPath": str(artifact), "contentType": content_type}
    result = {
        "status": "succeeded" if success else "failed",
        "summary": summary,
        "output": {"taskId": task_id, "success": success, "artifacts": artifacts},
    }
    if not success:
        result["error"] = {
            "code": "CLAWINSIGHT_BATCH_FAILED",
            "message": "ClawInsight batch failed; inspect the task artifacts",
            "retryable": True,
        }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True, help="shared AIS YAML; deployment context")
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--result-file", type=Path, required=True)
    args = parser.parse_args(argv)
    task = _object(args.task_file)
    task_id = task.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("taskId is required")
    task_output = args.output_dir.resolve()
    task_output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["OPENCLAW_STATE_DIR"] = str(task_output / "openclaw-state")
    os.environ["OPENCLAW_WORKSPACE"] = str(task_output / "openclaw-workspace")
    config = _resolved_config(_config_path(task, args.input_dir.resolve()), task_output)
    input_data = task.get("input", {})
    mode = input_data.get("mode", "dry-run")
    if mode not in {"dry-run", "apply"}:
        raise ValueError("input.mode must be dry-run or apply")
    batch_args = ["--config", str(config), "--prepare-nas", "--verbose", "--apply" if mode == "apply" else "--dry-run"]
    if isinstance(input_data.get("topPerLane"), int):
        batch_args += ["--top-per-lane", str(input_data["topPerLane"])]
    if isinstance(input_data.get("endDate"), str):
        batch_args += ["--end-date", input_data["endDate"]]
    try:
        exit_code = batch_main(batch_args)
    except Exception:
        _write_result(args.result_file, task_id, False, "ClawInsight batch failed", _latest_report(task_output))
        raise
    _write_result(args.result_file, task_id, exit_code == 0,
                  "ClawInsight batch completed" if exit_code == 0 else "ClawInsight batch failed",
                  _latest_report(task_output))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
