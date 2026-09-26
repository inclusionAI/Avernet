from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import logger
from ..integration.plan_source import build_plan_source
from ..models import CasePreference, Diagnosis
from .json_io import write_json


def write_plan_source(
    out: Path,
    safe_bot: str,
    bot_id: str,
    task_id: str,
    artifacts: dict[str, str],
    layout: dict[str, Any],
    pref: CasePreference,
    selected: list[Diagnosis],
    selection_report: dict[str, Any],
) -> Path:
    logger.info(
        "plan source build start",
        bot_id=bot_id,
        selected_count=len(selected),
        artifact_keys=sorted(artifacts.keys()),
        selection_status=selection_report.get("status"),
    )
    plan_source = build_plan_source(
        task_id=task_id,
        bot_id=bot_id,
        artifacts=artifacts,
        layout=layout,
        pref=pref,
        diags=selected,
        selection_report=selection_report,
    )
    merge_case_artifact_paths(plan_source, artifacts.get("diagnose_result_json", ""))
    plan_path = out / "plan-source.json"
    write_json(plan_path, plan_source)
    logger.info(
        "plan source write done",
        path=str(plan_path),
        case_count=len(plan_source.get("cases") or []),
        has_diagnose_result=bool(artifacts.get("diagnose_result_json")),
    )
    return plan_path


def merge_case_artifact_paths(plan_source: dict[str, Any], diagnose_result_path: str) -> None:
    try:
        path = Path(diagnose_result_path)
        if not path.exists():
            logger.warning(
                "plan source case artifact merge skipped",
                reason="diagnose_result_missing",
                diagnose_result_path=diagnose_result_path,
            )
            return
        result = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - case artifact paths are best-effort handoff metadata.
        logger.warning(
            "plan source case artifact merge failed",
            diagnose_result_path=diagnose_result_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    by_id = {
        str(c.get("case_id") or ""): c
        for c in result.get("cases") or []
        if isinstance(c, dict)
    }
    merged_count = 0
    for case in plan_source.get("cases") or []:
        if not isinstance(case, dict):
            continue
        extra = by_id.get(str(case.get("case_id") or ""))
        if not extra:
            continue
        evidence = case.setdefault("evidence", {})
        artifact_paths = evidence.setdefault("artifact_paths", {}) if isinstance(evidence, dict) else {}
        for key in (
            "case_dir",
            "raw_session_copy_path",
            "original_session_jsonl_path",
            "judge_result_path",
            "analysis_path",
        ):
            if extra.get(key):
                artifact_paths[key] = extra[key]
                merged_count += 1
    logger.info(
        "plan source case artifact merge done",
        diagnose_result_path=diagnose_result_path,
        known_case_count=len(by_id),
        merged_field_count=merged_count,
    )
