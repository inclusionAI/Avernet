from __future__ import annotations

from pathlib import Path
from typing import Any

from ..integration.artifact_publish import publish_final_artifacts, write_publish_result
from ..io import load_json

def _skip_final_artifacts(*, task_id: str, output_dir: Path) -> dict[str, Any]:
    result = {
        "status": "skipped",
        "enabled": False,
        "reason": "temporarily_disabled",
        "message": "OSS pack/publish is temporarily disabled in clawevolve-plan main flow; local plan artifacts and ClawWeb step report continue normally.",
        "task_id": task_id,
        "evolve_results_dir": str(output_dir),
        "result_path": str(output_dir / "oss_upload_result.json"),
    }
    write_publish_result(output_dir / "oss_upload_result.json", result)
    return result


def _publish_final_artifacts(
    *, plan: dict[str, Any] | None, task_id: str, output_dir: Path
) -> dict[str, Any]:
    if plan is None:
        objective_json = output_dir / "objective.json"
        if objective_json.exists():
            plan = load_json(objective_json)
        else:
            raise ValueError(
                f"Cannot publish existing plan artifacts without Plan Source or {objective_json}"
            )
    result_path = output_dir / "oss_upload_result.json"
    result = publish_final_artifacts(
        plan=plan,
        task_id=task_id,
        evolve_results_dir=output_dir,
    )
    write_publish_result(result_path, result)
    if result.get("status") != "ok":
        raise RuntimeError(
            "final artifact publish failed: " + str(result.get("error") or result)
        )
    return result
