from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import Diagnosis
from ..utils import replayability_issues


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
        + ("\n" if rows else "")
    )


def diagnosis_dict(d: Diagnosis) -> dict[str, Any]:
    return {
        "session_id": d.session.session_id,
        "session_path": d.session.path,
        "query": d.query,
        "original_query": d.original_query,
        "replayability_issues": replayability_issues(d.query),
        "replayability_checked": True,
        "original_model": d.session.original_model,
        "original_model_source": d.session.original_model_source,
        "case_type": d.case_type,
        "symptom_class": d.symptom_class,
        "root_cause_class": d.root_cause_class,
        "common_problem_key": d.common_problem_key,
        "evolution_failure_mode": d.evolution_failure_mode,
        "requires_search": d.requires_search,
        "root_cause_summary": d.root_cause_summary,
        "confidence": d.confidence,
        "quality_score": d.quality_score,
        "quality_notes": d.quality_notes,
        "tool_hints": d.tool_hints,
        "evidence_file_hints": d.evidence_file_hints,
        "evidence": d.evidence,
        "failure_controllability": d.failure_controllability,
        "optimization_value": d.optimization_value,
        "selection_bucket": d.selection_bucket,
        "selection_reason": d.selection_reason,
        "backfilled": d.backfilled,
        "intent_match": d.intent_match,
        "ocsa": d.ocsa,
        "eligibility": d.eligibility,
        "query_fidelity": d.query_fidelity,
    }
