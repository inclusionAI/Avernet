from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from ..artifacts.case_ids import case_id
from ..constants import MODE_OPTIMIZATION_GOALS, MODE_TARGET_HINTS
from ..models import CasePreference, Diagnosis
from ..utils import replayability_issues, utc_iso


def build_default_goal(pref: CasePreference) -> dict[str, Any]:
    return {
        "max_iterations": 10,
        "target_task_success_rate": 0.90,
        "regression_drop_max": 0.02,
        "case_timeout_seconds": pref.timeout_seconds,
        "goal_text": (
            f"最多10轮优化；任务完成率>=90%；good regression回退<=0.02；"
            f"每个case timeout={pref.timeout_seconds}s；搜索类case必须有检索过程、相关证据和正确证据使用。"
        ),
    }


def build_plan_source(
    *,
    task_id: str,
    bot_id: str,
    artifacts: dict[str, str],
    layout: dict[str, Any],
    pref: CasePreference,
    diags: list[Diagnosis],
    selection_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical Diagnose producer output consumed by Plan."""

    generated_at = utc_iso()
    selection = selection_report or {}
    user_intent = {
        "schema_version": "clawevolve-diagnose-intent.v1",
        "raw_request": pref.raw_message,
        "intent_text": pref.intent_text or pref.raw_message,
        "confidence": pref.intent_confidence,
    }
    title = str(user_intent["intent_text"] or "Diagnose 发现的 Bot 问题").strip()
    return {
        "schema_version": "plan-source/v2",
        "generated_at": generated_at,
        "source": {
            "type": "diagnose",
            "id": f"diagnose:{task_id or bot_id}",
            "producer": "clawevolve-diagnose",
            "bot_id": bot_id,
            "version": "2",
            "frozen_at": generated_at,
        },
        "problem": {
            "title": title,
            "user_guidance": pref.raw_message or None,
        },
        "cases": [
            _source_case(d, index, len(diags), pref)
            for index, d in enumerate(diags)
        ],
        "analysis": {
            "case_distribution": _case_distribution(diags),
            "root_cause_clusters": _clusters(diags),
        },
        "planning_hints": {
            "default_optimization_goal": build_default_goal(pref),
            "case_preference": _preference_dict(pref),
            "user_intent": user_intent,
            "timeout_seconds": pref.timeout_seconds,
        },
        "extensions": {
            "diagnose": {
                "diagnose_source": selection.get("diagnose_source", ""),
                "artifacts": dict(artifacts),
                "agent_context": layout,
                "selection_report": selection,
                "clawweb_domain": {
                    "enabled": False,
                    "status": "deferred_to_plan",
                },
            }
        },
    }


def _source_case(
    diagnosis: Diagnosis,
    index: int,
    total: int,
    pref: CasePreference,
) -> dict[str, Any]:
    mode = diagnosis.evolution_failure_mode
    return {
        "case_id": case_id(diagnosis),
        "case_type": diagnosis.case_type,
        "case_split": "validation" if index >= max(1, int(total * 0.8)) else "train",
        "session_id": diagnosis.session.session_id,
        "query": diagnosis.query,
        "context": {
            "source_session_id": diagnosis.session.session_id,
            "session_path": diagnosis.session.path,
            "source_file": diagnosis.session.path,
            "original_query": diagnosis.original_query,
            "original_model": diagnosis.session.original_model,
            "original_model_source": diagnosis.session.original_model_source,
            "replayability_issues": replayability_issues(diagnosis.query),
            "replayability_checked": True,
        },
        "evidence": {
            "items": diagnosis.evidence,
            "file_hints": diagnosis.evidence_file_hints,
            "artifact_paths": {},
        },
        "analysis": {
            "symptom_class": diagnosis.symptom_class,
            "root_cause_class": diagnosis.root_cause_class,
            "common_problem_key": diagnosis.common_problem_key,
            "root_cause_cluster_id": (
                diagnosis.common_problem_key
                or diagnosis.root_cause_class
                or mode
            ),
            "evolution_failure_mode": mode,
            "root_cause_summary": diagnosis.root_cause_summary,
            "problem_analysis": diagnosis.root_cause_summary,
            "confidence": diagnosis.confidence,
            "quality_score": diagnosis.quality_score,
            "quality_notes": diagnosis.quality_notes,
            "failure_controllability": diagnosis.failure_controllability,
            "optimization_value": diagnosis.optimization_value,
            "selection": {
                "bucket": diagnosis.selection_bucket,
                "reason": diagnosis.selection_reason,
                "backfilled": diagnosis.backfilled,
            },
        },
        "planning_hints": {
            "optimization_goal": MODE_OPTIMIZATION_GOALS.get(
                mode, MODE_OPTIMIZATION_GOALS["unknown_failure_mode"]
            ),
            "allowed_update_targets": MODE_TARGET_HINTS.get(
                mode, MODE_TARGET_HINTS["unknown_failure_mode"]
            ),
            "tool_hints": diagnosis.tool_hints,
            "requires_search": diagnosis.requires_search,
            "timeout_seconds": pref.timeout_seconds,
        },
    }


def _preference_dict(pref: CasePreference) -> dict[str, Any]:
    return {
        "raw_message": pref.raw_message,
        "intent_text": pref.intent_text,
        "intent_confidence": pref.intent_confidence,
        "case_limit": pref.case_limit,
        "target_failure_modes": pref.normalized_modes(),
        "include_good": pref.include_good,
        "bad_case_count": pref.bad_case_count,
        "good_case_count": pref.good_case_count,
        "dataset_profile": pref.dataset_profile,
        "since": pref.since,
        "until": pref.until,
        "time_range_label": pref.time_range_label,
        "focus_terms": pref.focus_terms,
        "required_terms": pref.required_terms,
        "excluded_terms": pref.excluded_terms,
        "scoring_requirements": pref.scoring_requirements,
        "timeout_seconds": pref.timeout_seconds,
        "drop_context_dependent": pref.drop_context_dependent,
        "rewrite_multi_sentence_query": pref.rewrite_multi_sentence_query,
        "search_evidence_required_for_high_score": pref.search_evidence_required_for_high_score,
        "stable_run_required": pref.stable_run_required,
    }


def _case_distribution(diags: list[Diagnosis]) -> dict[str, Any]:
    by_mode = Counter(d.evolution_failure_mode for d in diags)
    return {
        "total": len(diags),
        "by_original_model": dict(
            Counter(d.session.original_model or "unknown" for d in diags)
        ),
        "bad": sum(d.case_type == "bad" for d in diags),
        "good_regression": sum(d.case_type == "good" for d in diags),
        "search_required": sum(d.requires_search for d in diags),
        "by_failure_mode": dict(by_mode),
    }


def _clusters(diags: list[Diagnosis]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Diagnosis]] = defaultdict(list)
    for diagnosis in diags:
        grouped[diagnosis.evolution_failure_mode].append(diagnosis)
    clusters: list[dict[str, Any]] = []
    for mode, items in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        examples = items[:5]
        evidence_hints = _unique_dicts(
            [hint for diagnosis in items for hint in diagnosis.evidence_file_hints or diagnosis.evidence]
        )[:8]
        tool_hints = _unique([hint for diagnosis in items for hint in diagnosis.tool_hints])[:8]
        clusters.append(
            {
                "evolution_failure_mode": mode,
                "affected_case_count": len(items),
                "example_case_ids": [case_id(item) for item in examples],
                "example_queries": [item.query for item in examples[:3]],
                "problem_analysis": _problem_analysis(mode, items),
                "optimization_goal": MODE_OPTIMIZATION_GOALS.get(
                    mode, MODE_OPTIMIZATION_GOALS["unknown_failure_mode"]
                ),
                "allowed_update_targets_hint": MODE_TARGET_HINTS.get(
                    mode, MODE_TARGET_HINTS["unknown_failure_mode"]
                ),
                "evidence_file_hints": evidence_hints,
                "tool_hints": tool_hints,
                "average_quality_score": round(
                    sum(item.quality_score for item in items) / max(1, len(items)), 3
                ),
            }
        )
    return clusters


def _problem_analysis(mode: str, items: list[Diagnosis]) -> str:
    summary = items[0].root_cause_summary if items else ""
    search_count = sum(item.requires_search for item in items)
    return f"{len(items)} 个 case 聚合为 `{mode}`。代表根因：{summary} 搜索相关 case 数：{search_count}。"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _unique_dicts(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for item in items:
        path = str(item.get("path") or "").strip()
        key = path + "|" + str(item.get("term") or item.get("source") or "")
        if path and key not in seen:
            result.append(
                {
                    name: str(value)
                    for name, value in item.items()
                    if name in {"source", "path", "term", "snippet"}
                }
            )
            seen.add(key)
    return result
