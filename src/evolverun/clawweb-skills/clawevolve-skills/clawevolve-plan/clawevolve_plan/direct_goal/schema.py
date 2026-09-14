from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from pathlib import Path
from typing import Any

from ..discovery.schema import DiscoveryResult, validate_discovery_payload

SCHEMA_VERSION = "clawevolve.plan.direct-goal.v1"
PLAN_SOURCE_SCHEMA_VERSION = "plan-source/v2"


@dataclass(frozen=True)
class DirectGoalPayload:
    raw: dict[str, Any]
    goal_analysis: dict[str, Any]
    cases: list[dict[str, Any]]
    discovery: DiscoveryResult
    workspace_root: Path


def goal_digest(goal: str) -> str:
    return hashlib.sha256(str(goal).encode("utf-8")).hexdigest()


def requested_bench_template_count(goal: str) -> int | None:
    text = str(goal or "")
    patterns = (
        r"(\d+)\s*个?\s*bench\s*(?:templates?|cases?)",
        r"bench\s*(?:templates?|cases?)\s*(?:数量|数|count|of)?\s*(?:为|是|=|:)?\s*(\d+)",
        r"(\d+)\s*个?\s*(?:评测|测试|bench)\s*(?:模板|用例|cases?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = int(match.group(1))
            return value if value > 0 else None
    return None


def requested_task_success_rate(goal: str) -> float | None:
    text = str(goal or "")
    patterns = (
        r"(?:任务)?成功率[^%\d]{0,12}(\d+(?:\.\d+)?)\s*%",
        r"success\s*rate[^%\d]{0,12}(\d+(?:\.\d+)?)\s*%",
        r"(\d+(?:\.\d+)?)\s*%[^。；,，]{0,12}(?:任务)?成功率",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = float(match.group(1))
            if 0 < value <= 100:
                return value / 100.0
    return None


def validate_direct_goal_payload(
    payload: Any,
    *,
    goal: str,
    workspace_root: Path,
) -> DirectGoalPayload:
    if not isinstance(payload, dict):
        raise ValueError("direct_goal.json must contain a JSON object")
    problems: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    if str(payload.get("goal_digest") or "") != goal_digest(goal):
        problems.append("goal_digest does not match the current --goal")
    declared_workspace = str(payload.get("workspace_root") or "").strip()
    if declared_workspace:
        try:
            if (
                Path(declared_workspace).expanduser().resolve()
                != workspace_root.resolve()
            ):
                problems.append("workspace_root does not match the resolved workspace")
        except OSError:
            problems.append("workspace_root is invalid")
    analysis = payload.get("goal_analysis")
    if not isinstance(analysis, dict):
        problems.append("goal_analysis must be an object")
        analysis = {}
    if not str(analysis.get("raw_goal") or "").strip():
        problems.append("goal_analysis.raw_goal is required")
    for key in ("task_scope", "desired_outcome"):
        if not str(analysis.get(key) or "").strip():
            problems.append(f"goal_analysis.{key} is required")
    cases = payload.get("prospective_cases")
    if not isinstance(cases, list) or not cases:
        problems.append("prospective_cases must be a non-empty list")
        cases = []
    normalized_cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(cases):
        if not isinstance(item, dict):
            problems.append(f"prospective_cases[{index}] must be an object")
            continue
        case = dict(item)
        if str(case.get("case_type") or "prospective").strip() != "prospective":
            problems.append(f"prospective_cases[{index}].case_type must be prospective")
        if case.get("session_id") or case.get("source_session_id"):
            problems.append(
                f"prospective_cases[{index}] must not claim a historical session"
            )
        case_id = str(case.get("case_id") or "").strip()
        if not case_id:
            problems.append(f"prospective_cases[{index}].case_id is required")
        elif case_id in seen_ids:
            problems.append(f"duplicate prospective case_id: {case_id}")
        else:
            seen_ids.add(case_id)
        for key in ("query", "scenario", "expected_behavior", "failure_mode"):
            if not str(case.get(key) or "").strip():
                problems.append(f"prospective_cases[{index}].{key} is required")
        for key in ("success_criteria", "scoring_hints", "forbidden_behavior"):
            if not _strings(case.get(key)):
                problems.append(
                    f"prospective_cases[{index}].{key} must be a non-empty list"
                )
        normalized_cases.append(case)
    requested_count = requested_bench_template_count(goal)
    if requested_count is not None and len(normalized_cases) != requested_count:
        problems.append(
            f"prospective_cases must contain exactly {requested_count} cases because --goal "
            f"requests {requested_count} bench templates"
        )
    if problems:
        raise ValueError("Invalid direct goal schema: " + "; ".join(problems))

    discovery = validate_discovery_payload(
        payload.get("discovery"),
        workspace_root=workspace_root,
        lenient_model_output=True,
        allow_open_skills_creation_scope=(
            os.environ.get("CLAWWEB_VERSION") == "openversion"
        ),
    )
    discovery_workspace = str(discovery.raw.get("workspace_root") or "").strip()
    if discovery_workspace and (
        Path(discovery_workspace).expanduser().resolve() != workspace_root.resolve()
    ):
        raise ValueError(
            "Invalid direct goal discovery: workspace_root does not match the resolved workspace"
        )
    finding_ids = {
        str(item.get("case_id") or "").strip()
        for item in discovery.raw.get("case_findings") or []
        if isinstance(item, dict)
    }
    missing_findings = sorted(seen_ids - finding_ids)
    if missing_findings:
        raise ValueError(
            "Invalid direct goal discovery: missing case_findings for "
            + ", ".join(missing_findings)
        )
    unknown_related_ids = sorted(
        {
            str(case_id).strip()
            for finding in discovery.raw.get("target_findings") or []
            if isinstance(finding, dict)
            for case_id in finding.get("related_case_ids") or []
            if str(case_id).strip() and str(case_id).strip() not in seen_ids
        }
    )
    if unknown_related_ids:
        raise ValueError(
            "Invalid direct goal discovery: target_findings reference unknown cases: "
            + ", ".join(unknown_related_ids)
        )
    return DirectGoalPayload(
        raw=payload,
        goal_analysis=dict(analysis),
        cases=normalized_cases,
        discovery=discovery,
        workspace_root=workspace_root.resolve(),
    )


def normalize_direct_goal_source(
    payload: DirectGoalPayload,
    *,
    task_id: str,
    bot_id: str,
) -> dict[str, Any]:
    cases = [_normalize_case(item) for item in payload.cases]
    clusters = _build_clusters(cases, payload.goal_analysis)
    raw_goal = str(payload.goal_analysis.get("raw_goal") or "").strip()
    success_rate = requested_task_success_rate(raw_goal)
    default_goal = {"goal_text": raw_goal}
    if success_rate is not None:
        default_goal["target_task_success_rate"] = success_rate
    discovery_raw = payload.discovery.raw
    planned_deliverables = list(discovery_raw.get("planned_deliverables") or [])
    reference_files = list(discovery_raw.get("reference_files") or [])
    creation_scopes = _unique_strings(
        item.get("path")
        for item in discovery_raw.get("target_findings") or []
        if isinstance(item, dict)
        and str(item.get("target_type") or "").strip() == "creation_scope"
    )
    return {
        "schema_version": PLAN_SOURCE_SCHEMA_VERSION,
        "generated_at": str(payload.raw.get("generated_at") or "direct-goal"),
        "source": {
            "type": "direct_goal",
            "id": f"direct-goal:{task_id}",
            "producer": "clawevolve-plan-direct-goal",
            "bot_id": bot_id or "current-bot",
            "version": "2",
        },
        "problem": {"title": raw_goal, "user_guidance": raw_goal},
        "cases": cases,
        "analysis": {
            "root_cause_clusters": clusters,
            "case_distribution": {
                "total": len(cases),
                "prospective": len(cases),
                "good": 0,
                "bad": 0,
            },
        },
        "planning_hints": {
            "user_intent": {
                "raw_request": raw_goal,
                "intent_text": raw_goal,
                "confidence": 1.0,
            },
            "case_preference": {
                "raw_message": raw_goal,
                "intent_text": raw_goal,
            },
            "default_optimization_goal": default_goal,
        },
        "extensions": {
            "direct_goal": {
                "agent_context": {
                    "workspace_root": str(payload.workspace_root),
                    "creation_scopes": creation_scopes,
                    "reference_files": reference_files,
                    "planned_deliverables": planned_deliverables,
                },
                "goal_context": payload.goal_analysis,
                "direct_goal_analysis": payload.raw,
                "creation_scopes": creation_scopes,
                "reference_files": reference_files,
                "planned_deliverables": planned_deliverables,
                "artifacts": {},
            }
        },
    }


def _normalize_case(item: dict[str, Any]) -> dict[str, Any]:
    failure_mode = str(item.get("failure_mode") or "unknown_failure_mode").strip()
    query = str(item.get("query") or "").strip()
    return {
        "case_id": str(item.get("case_id") or "").strip(),
        "case_type": "prospective",
        "case_split": str(item.get("case_split") or "train").strip(),
        "query": query,
        "context": {
            "original_query": query,
            "scenario": str(item.get("scenario") or "").strip(),
        },
        "evidence": {
            "items": [
                "Prospective case generated from the explicit user goal; no historical session evidence."
            ],
            "source": "user_goal",
            "file_hints": [],
            "artifact_paths": {},
        },
        "analysis": {
            "evolution_failure_mode": failure_mode,
            "problem_analysis": str(item.get("scenario") or "").strip(),
            "optimization_value": str(item.get("optimization_value") or "high"),
            "failure_controllability": str(item.get("failure_controllability") or "high"),
        },
        "planning_hints": {
            "expected_behavior": str(item.get("expected_behavior") or "").strip(),
            "forbidden_behavior": _strings(item.get("forbidden_behavior")),
            "success_criteria": _strings(item.get("success_criteria")),
            "scoring_hints": _strings(item.get("scoring_hints")),
        },
    }


def _build_clusters(
    cases: list[dict[str, Any]], goal_analysis: dict[str, Any]
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        analysis = case.get("analysis") if isinstance(case.get("analysis"), dict) else {}
        grouped.setdefault(
            str(analysis.get("evolution_failure_mode") or "unknown_failure_mode"), []
        ).append(case)
    desired = str(goal_analysis.get("desired_outcome") or "").strip()
    clusters: list[dict[str, Any]] = []
    for index, (mode, items) in enumerate(grouped.items(), start=1):
        clusters.append(
            {
                "cluster_id": f"goal-cluster-{index:03d}",
                "evolution_failure_mode": mode,
                "summary": f"围绕用户目标补齐 `{mode}` 相关能力。",
                "problem_analysis": "；".join(
                    str((item.get("context") or {}).get("scenario") or "").strip()
                    for item in items
                ),
                "optimization_goal": desired,
                "affected_case_count": len(items),
                "example_case_ids": [str(item["case_id"]) for item in items],
                "example_queries": [str(item["query"]) for item in items],
                "source": "direct_goal",
            }
        )
    return clusters


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
