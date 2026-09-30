from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .. import logger
from ..artifacts.case_ids import case_id
from ..artifacts.outputs import diagnosis_dict, write_jsonl
from ..judge.ocsa_session_report_analyzer import load_session_payload
from ..models import CasePreference, Diagnosis
from ..utils import replayability_issues
from .json_io import write_json


def write_case_artifacts(
    out_dir: Path,
    safe_bot: str,
    diagnoses: list[Diagnosis],
    selected: list[Diagnosis],
    pref: CasePreference,
) -> dict[str, str]:
    """Write diagnosis/eval JSONL, per-case evidence folders, and manifest."""

    diagnosis_path = out_dir / f"{safe_bot}_diagnosis.jsonl"
    eval_queries_path = out_dir / f"{safe_bot}_eval_queries.jsonl"
    eval_cases_path = out_dir / f"{safe_bot}_eval_cases.jsonl"

    logger.info(
        "case artifacts core jsonl write start",
        out_dir=str(out_dir),
        diagnosis_count=len(diagnoses),
        selected_count=len(selected),
        diagnosis_path=str(diagnosis_path),
        eval_queries_path=str(eval_queries_path),
        eval_cases_path=str(eval_cases_path),
    )
    write_jsonl(diagnosis_path, [diagnosis_dict(d) for d in diagnoses])
    write_jsonl(eval_queries_path, [_eval_query_row(d) for d in selected])
    eval_case_rows = [
        _eval_case_row(d, index, len(selected), pref)
        for index, d in enumerate(selected)
    ]
    write_jsonl(eval_cases_path, eval_case_rows)

    cases_dir = out_dir / "diagnose_cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    manifest_cases = _write_per_case_folders(cases_dir, selected, eval_case_rows)

    manifest_path = out_dir / f"{safe_bot}_diagnose_result.json"
    logger.info(
        "case artifacts per-case folders written",
        cases_dir=str(cases_dir),
        manifest_case_count=len(manifest_cases),
        case_ids=[case.get("case_id") for case in manifest_cases],
    )
    write_json(
        manifest_path,
        {
            "schema_version": "clawevolve-diagnose-result.v1",
            "diagnosis_jsonl": str(diagnosis_path),
            "eval_queries_jsonl": str(eval_queries_path),
            "eval_cases_jsonl": str(eval_cases_path),
            "case_count": len(selected),
            "cases_dir": str(cases_dir),
            "cases": manifest_cases,
        },
    )
    logger.info(
        "case artifacts manifest written",
        manifest_path=str(manifest_path),
        case_count=len(selected),
    )
    return {
        "diagnosis_jsonl": str(diagnosis_path),
        "eval_queries_jsonl": str(eval_queries_path),
        "eval_cases_jsonl": str(eval_cases_path),
        "diagnose_result_json": str(manifest_path),
        "diagnose_cases_dir": str(cases_dir),
    }


def _write_per_case_folders(
    cases_dir: Path,
    selected: list[Diagnosis],
    eval_case_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    manifest_cases: list[dict[str, Any]] = []
    for index, diagnosis in enumerate(selected):
        row = eval_case_rows[index]
        logger.info(
            "case artifact folder write start",
            index=f"{index + 1}/{len(selected)}",
            case_id=row.get("case_id"),
            session_id=diagnosis.session.session_id,
            case_type=diagnosis.case_type,
            failure_mode=diagnosis.evolution_failure_mode,
        )
        case_dir = cases_dir / row["case_id"]
        case_dir.mkdir(parents=True, exist_ok=True)
        raw_session_path = case_dir / "session.json"
        original_session_path = _copy_original_session_jsonl(diagnosis, case_dir)
        judge_result_path = case_dir / "judge_result.json"
        analysis_path = case_dir / "analysis.md"
        session_payload = diagnosis.session.raw_session or load_session_payload(
            diagnosis.session
        )
        write_json(
            raw_session_path,
            session_payload
            or {
                "source_path": diagnosis.session.path,
                "raw_text": diagnosis.session.raw_text[:20000],
            },
        )
        write_json(judge_result_path, _judge_result_record(diagnosis))
        analysis_path.write_text(_case_analysis_markdown(diagnosis), encoding="utf-8")
        manifest_cases.append(
            row
            | {
                "case_dir": str(case_dir),
                "raw_session_copy_path": str(raw_session_path),
                "original_session_jsonl_path": str(original_session_path) if original_session_path else "",
                "judge_result_path": str(judge_result_path),
                "analysis_path": str(analysis_path),
            }
        )
        logger.info(
            "case artifact folder write done",
            case_id=row.get("case_id"),
            case_dir=str(case_dir),
            raw_session_copy_path=str(raw_session_path),
            original_session_jsonl_path=str(original_session_path) if original_session_path else "",
            judge_result_path=str(judge_result_path),
            analysis_path=str(analysis_path),
        )
    return manifest_cases


def _copy_original_session_jsonl(diag: Diagnosis, case_dir: Path) -> Path | None:
    source_value = str(diag.session.path or "").strip()
    if not source_value:
        logger.warning(
            "original session jsonl copy skipped",
            reason="empty_session_path",
            case_id=case_id(diag),
            session_id=diag.session.session_id,
        )
        return None
    source = Path(source_value).expanduser()
    if not source.exists() or not source.is_file():
        logger.warning(
            "original session jsonl copy skipped",
            reason="source_missing_or_not_file",
            case_id=case_id(diag),
            session_id=diag.session.session_id,
            source_path=str(source),
        )
        return None
    suffix = source.suffix if source.suffix else ".jsonl"
    dest = case_dir / f"original_session{suffix}"
    try:
        if source.resolve() == dest.resolve():
            logger.info(
                "original session jsonl copy skipped",
                reason="source_equals_dest",
                case_id=case_id(diag),
                session_id=diag.session.session_id,
                path=str(dest),
            )
            return dest
    except OSError:
        pass
    try:
        shutil.copy2(source, dest)
    except Exception as exc:  # noqa: BLE001 - keep artifact writing best-effort.
        logger.warning(
            "original session jsonl copy failed",
            case_id=case_id(diag),
            session_id=diag.session.session_id,
            source_path=str(source),
            dest_path=str(dest),
            error=f"{type(exc).__name__}: {exc}",
        )
        return None
    logger.info(
        "original session jsonl copied",
        case_id=case_id(diag),
        session_id=diag.session.session_id,
        source_path=str(source),
        dest_path=str(dest),
        bytes=dest.stat().st_size if dest.exists() else 0,
    )
    return dest


def _judge_result_record(diag: Diagnosis) -> dict[str, Any]:
    return {
        "source": "diagnose_session_judge_result",
        "session_id": diag.session.session_id,
        "case_type": diag.case_type,
        "symptom_class": diag.symptom_class,
        "root_cause_class": diag.root_cause_class,
        "common_problem_key": diag.common_problem_key,
        "evolution_failure_mode": diag.evolution_failure_mode,
        "query": diag.query,
        "original_query": diag.original_query,
        "replayability_issues": replayability_issues(diag.query),
        "replayability_checked": True,
        "root_cause_summary": diag.root_cause_summary,
        "confidence": diag.confidence,
        "quality_score": diag.quality_score,
        "quality_notes": diag.quality_notes,
        "tool_hints": diag.tool_hints,
        "evidence": diag.evidence,
        "intent_match": diag.intent_match,
        "ocsa": diag.ocsa,
        "eligibility": diag.eligibility,
        "query_fidelity": diag.query_fidelity,
    }


def _case_analysis_markdown(diag: Diagnosis) -> str:
    return "\n".join(
        [
            f"# Diagnose Case Analysis: {case_id(diag)}",
            "",
            f"- Session id：`{diag.session.session_id}`",
            f"- Session path：`{diag.session.path}`",
            f"- Case type：`{diag.case_type}`",
            f"- Failure mode：`{diag.evolution_failure_mode}`",
            f"- Root cause class：`{diag.root_cause_class}`",
            f"- Query：{diag.query}",
            f"- Original query：{diag.original_query or 'N/A'}",
            f"- Replayability issues：{replayability_issues(diag.query) or 'none'}",
            f"- Quality score：{diag.quality_score:.3f}",
            "",
            "## Session Judge 分析结论",
            "",
            diag.root_cause_summary or "N/A",
            "",
            "## 选择原因",
            "",
            diag.selection_reason or "N/A",
            "",
            "## Evidence",
            "",
            *[
                f"- `{e.get('source','')}` `{e.get('path','')}`：{e.get('snippet','')}"
                for e in (diag.evidence or [])
            ],
            "",
        ]
    )


def _eval_query_row(diag: Diagnosis) -> dict[str, Any]:
    return {
        "case_id": case_id(diag),
        "query": diag.query,
        "original_query": diag.original_query,
        "replayability_issues": replayability_issues(diag.query),
        "replayability_checked": True,
        "original_model": diag.session.original_model,
        "original_model_source": diag.session.original_model_source,
        "case_type": diag.case_type,
        "requires_search": diag.requires_search,
        "failure_mode": diag.evolution_failure_mode,
        "quality_score": diag.quality_score,
        "failure_controllability": diag.failure_controllability,
        "optimization_value": diag.optimization_value,
        "selection_bucket": diag.selection_bucket,
        "selection_reason": diag.selection_reason,
        "backfilled": diag.backfilled,
    }


def _eval_case_row(
    diag: Diagnosis,
    index: int,
    total: int,
    pref: CasePreference,
) -> dict[str, Any]:
    return diagnosis_dict(diag) | {
        "case_id": case_id(diag),
        "case_split": _case_split(index, total),
        "timeout_seconds": pref.timeout_seconds,
    }


def _case_split(index: int, total: int) -> str:
    return "validation" if index >= max(1, int(total * 0.8)) else "train"
