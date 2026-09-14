from __future__ import annotations

from typing import Any
from pathlib import Path

from ..acquisition.tracing import enrich_environment_evidence
from ..models import CasePreference, Diagnosis, JudgeRuntimeConfig, SessionRow
from ..selection import annotate_optimization_metadata, select_diagnoses
from ..judge.local_session_judge_provider import LocalSessionJudgeProvider
from .progress import progress


def _diagnose_and_sample(
    rows: list[SessionRow],
    bot_id: str,
    pref: CasePreference,
    layout: dict[str, Any],
    judge_runtime: JudgeRuntimeConfig,
    output_dir: Path,
) -> tuple[list[Diagnosis], list[Diagnosis], dict[str, Any]]:
    """Run the selected session judge, sample eval cases, and enrich evidence."""

    progress("diagnose source selected", source="local", input_sessions=len(rows))
    judge_result = LocalSessionJudgeProvider(
        judge_runtime, artifact_dir=output_dir / "judge"
    ).analyze_until_selectable(
        rows, bot_id, pref
    )
    diagnoses = judge_result.diagnoses
    progress(
        "diagnosis lookup done",
        source="local",
        diagnosis_count=len(diagnoses),
        judge_stop_reason=(judge_result.stats or {}).get("judge_stop_reason", ""),
        judged_session_count=(judge_result.stats or {}).get("judged_session_count", 0),
        failed_session_count=(judge_result.stats or {}).get("failed_session_count", 0),
    )
    for diagnosis in diagnoses:
        annotate_optimization_metadata(diagnosis)
    progress("selection start", diagnosis_count=len(diagnoses), case_limit=pref.case_limit)
    selection = select_diagnoses(diagnoses, pref)
    selected = selection.selected
    progress(
        "selection done",
        selected=len(selected),
        status=selection.report.get("status"),
        selected_by_case_type=selection.report.get("selected_by_case_type"),
        selected_by_failure_mode=selection.report.get("selected_by_failure_mode"),
        quota_underfilled=selection.report.get("quota_underfilled"),
        post_ocsa_dedupe_enabled=False,
        coverage_note=selection.report.get("coverage_note"),
    )
    progress("environment evidence enrichment start", selected=len(selected))
    enrich_environment_evidence(selected, layout)
    progress("environment evidence enrichment done", selected=len(selected))
    report = dict(selection.report)
    report["diagnose_source"] = "local"
    report["judge_lookup"] = judge_result.stats
    if judge_result.warnings:
        report["judge_lookup_warnings"] = judge_result.warnings
    return diagnoses, selected, report
