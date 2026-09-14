from __future__ import annotations

from pathlib import Path
from typing import Any

from .contract import validate_plan_source


def build_planning_context(
    source: dict[str, Any], source_path: str | Path
) -> dict[str, Any]:
    """Project one canonical Plan Source into Plan's internal semantic model.

    This is the only seam between the producer contract and the Plan pipeline.
    It is source-agnostic: Diagnose, Insight Improvement, and Direct Goal all
    cross the same interface before Discovery and Spec generation.
    """

    document = validate_plan_source(source)
    source_meta = document["source"]
    problem = document["problem"]
    analysis = document["analysis"]
    planning = document["planning_hints"]
    extensions = document["extensions"]
    target_context = _target_context(planning)
    execution_target = target_context.get("execution_target", {})
    producer_extension = extensions.get(source_meta["type"])
    if not isinstance(producer_extension, dict):
        producer_extension = {}

    guidance = str(problem.get("user_guidance") or "").strip()
    goal_text = str(problem["title"]).strip()
    if guidance:
        goal_text += f"；{guidance}"
    configured_goal = planning.get("default_optimization_goal")
    default_goal = dict(configured_goal) if isinstance(configured_goal, dict) else {}
    default_goal.setdefault("goal_text", goal_text)

    agent_context = producer_extension.get("agent_context")
    if not isinstance(agent_context, dict):
        agent_context = {}
    artifacts = producer_extension.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
    selection_report = producer_extension.get("selection_report")
    if not isinstance(selection_report, dict):
        selection_report = {}
    clawweb_domain = producer_extension.get("clawweb_domain")
    if not isinstance(clawweb_domain, dict):
        clawweb_domain = {}
    case_preference = planning.get("case_preference")
    if not isinstance(case_preference, dict):
        case_preference = {}
    user_intent = planning.get("user_intent")
    if not isinstance(user_intent, dict):
        user_intent = {
            "raw_request": guidance,
            "intent_text": guidance or str(problem["title"]),
        }

    context = {
        "schema_version": document["schema_version"],
        "generated_at": document["generated_at"],
        "input_mode": source_meta["type"],
        "bot_id": execution_target.get("bot_id") or source_meta["bot_id"],
        "source_bot_id": source_meta["bot_id"],
        "source_skill": source_meta["producer"],
        "source_type": source_meta["type"],
        "problem": problem,
        "cases": [_planning_case(item) for item in document["cases"]],
        "case_distribution": analysis["case_distribution"],
        "root_cause_clusters": analysis["root_cause_clusters"],
        "default_optimization_goal": default_goal,
        "case_preference": case_preference,
        "user_intent": user_intent,
        "agent_context": {
            **agent_context,
            **({"target_context": target_context} if target_context else {}),
        },
        "artifacts": {**artifacts, "plan_source": str(source_path)},
        "analysis_report": artifacts.get("analysis_report_md", ""),
        "diagnose_result": artifacts.get("diagnose_result_json", ""),
        "diagnose_cases_dir": artifacts.get("diagnose_cases_dir", ""),
        "selection_report": selection_report,
        "clawweb_domain": clawweb_domain,
        "plan_source_ref": {
            "schema_version": document["schema_version"],
            "path": str(source_path),
            "source": dict(source_meta),
            "case_count": len(document["cases"]),
        },
        "extensions": extensions,
    }
    if source_meta["type"] == "direct_goal":
        context.update(
            {
                "goal_context": producer_extension.get("goal_context") or {},
                "creation_scopes": producer_extension.get("creation_scopes") or [],
                "reference_files": producer_extension.get("reference_files") or [],
                "planned_deliverables": producer_extension.get("planned_deliverables") or [],
                "direct_goal_analysis": producer_extension.get("direct_goal_analysis") or {},
            }
        )
    return context


def _planning_case(source_case: dict[str, Any]) -> dict[str, Any]:
    context = source_case.get("context")
    if not isinstance(context, dict):
        context = {}
    analysis = source_case.get("analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    planning = source_case.get("planning_hints")
    if not isinstance(planning, dict):
        planning = {}
    selection = analysis.get("selection")
    if not isinstance(selection, dict):
        selection = {}
    evidence = source_case.get("evidence")
    evidence_items = (
        evidence.get("items")
        if isinstance(evidence, dict) and "items" in evidence
        else _compact_external_evidence(evidence)
    )
    evidence_hints = evidence.get("file_hints") if isinstance(evidence, dict) else None
    artifact_paths = evidence.get("artifact_paths") if isinstance(evidence, dict) else None
    if not isinstance(artifact_paths, dict):
        artifact_paths = {}
    root_cause_summary = str(analysis.get("root_cause_summary") or "")
    return {
        "case_id": source_case.get("case_id"),
        "case_type": source_case.get("case_type"),
        "case_split": source_case.get("case_split") or "train",
        "ordinal": source_case.get("ordinal"),
        "session_id": source_case.get("session_id"),
        "source_session_id": context.get("source_session_id") or source_case.get("session_id"),
        "task_index": source_case.get("task_index"),
        "query": source_case.get("query"),
        "evidence": evidence_items,
        "evidence_file_hints": evidence_hints or [],
        **context,
        **artifact_paths,
        "symptom_class": analysis.get("symptom_class"),
        "root_cause_class": analysis.get("root_cause_class"),
        "common_problem_key": analysis.get("common_problem_key"),
        "root_cause_cluster_id": analysis.get("root_cause_cluster_id"),
        "evolution_failure_mode": analysis.get("evolution_failure_mode") or "unknown_failure_mode",
        "failure_class": analysis.get("failure_class"),
        "judge_summary": analysis.get("judge_summary"),
        "root_cause_summary": root_cause_summary,
        "problem_analysis": analysis.get("problem_analysis") or root_cause_summary,
        "confidence": analysis.get("confidence"),
        "quality_score": analysis.get("quality_score"),
        "quality_notes": analysis.get("quality_notes"),
        "failure_controllability": analysis.get("failure_controllability"),
        "optimization_value": analysis.get("optimization_value"),
        "selection_bucket": selection.get("bucket"),
        "selection_reason": selection.get("reason"),
        "backfilled": selection.get("backfilled"),
        "optimization_goal": planning.get("optimization_goal"),
        "allowed_update_targets_hint": planning.get("allowed_update_targets"),
        "tool_hints": planning.get("tool_hints"),
        "requires_search": planning.get("requires_search", False),
        "timeout_seconds": planning.get("timeout_seconds"),
        "scenario": context.get("scenario"),
        "expected_behavior": planning.get("expected_behavior"),
        "forbidden_behavior": planning.get("forbidden_behavior"),
        "success_criteria": planning.get("success_criteria"),
        "scoring_hints": planning.get("scoring_hints"),
    }


def _compact_external_evidence(evidence: Any) -> Any:
    """Keep planning signal without copying complete conversations into prompts."""

    if not isinstance(evidence, dict):
        return evidence
    compact = {
        key: evidence[key]
        for key in (
            "schema_version",
            "batch_id",
            "dt",
            "generated_at",
            "message_range",
            "source_task",
            "judge_meta",
            "payload_ref",
            "payload_etag",
            "payload_version_id",
        )
        if key in evidence
    }
    messages = evidence.get("messages")
    if isinstance(messages, list):
        compact["message_count"] = len(messages)
        compact["message_samples"] = [
            {
                "message_index": item.get("message_index"),
                "role": item.get("role"),
                "content": str(item.get("content") or "")[:300],
            }
            for item in _message_samples(messages)
            if isinstance(item, dict)
        ]
    return compact


def _message_samples(messages: list[Any]) -> list[Any]:
    """Bound prompt evidence while retaining both the start and outcome signal."""

    if len(messages) <= 3:
        return messages
    return [messages[0], messages[1], messages[-1]]


def _target_context(planning: dict[str, Any]) -> dict[str, Any]:
    context = planning.get("target_context")
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise ValueError("planning_hints.target_context must be an object")
    relationship = context.get("relationship")
    source_bot = context.get("source_bot")
    execution_target = context.get("execution_target")
    if relationship not in {"same_bot", "cross_bot"}:
        raise ValueError("planning_hints.target_context.relationship is unsupported")
    if not isinstance(source_bot, dict) or not isinstance(execution_target, dict):
        raise ValueError("planning_hints.target_context requires source_bot and execution_target")
    for label, value in (("source_bot", source_bot), ("execution_target", execution_target)):
        if not str(value.get("owner_user_id") or "").strip() or not str(value.get("bot_id") or "").strip():
            raise ValueError(f"planning_hints.target_context.{label} identity is incomplete")
    same_identity = (
        str(source_bot.get("owner_user_id")).strip()
        == str(execution_target.get("owner_user_id")).strip()
        and str(source_bot.get("bot_id")).strip()
        == str(execution_target.get("bot_id")).strip()
    )
    expected = "same_bot" if same_identity else "cross_bot"
    if relationship != expected:
        raise ValueError("planning_hints.target_context relationship does not match source/target identities")
    applicability_required = context.get("applicability_required")
    if not isinstance(applicability_required, bool) or applicability_required != (relationship == "cross_bot"):
        raise ValueError("planning_hints.target_context applicability flag is inconsistent")
    return context
