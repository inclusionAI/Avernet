from __future__ import annotations

import re
from typing import Any


_PERCENT_METRIC_PATTERNS = (
    re.compile(
        r"(?P<label>(?:MCP\s*(?:调用|call)?|任务|task\s*)?(?:调用)?(?:成功率|完成率|success\s*rate|completion\s*rate))"
        r"\s*(?:要达到|达到|达|为|至|不低于|至少|>=|≥|:|：|=)?\s*(?P<value>\d+(?:\.\d+)?)\s*%",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<value>\d+(?:\.\d+)?)\s*%\s*(?P<label>(?:MCP\s*(?:调用|call)?|任务|task\s*)?(?:调用)?(?:成功率|完成率|success\s*rate|completion\s*rate))",
        re.IGNORECASE,
    ),
)


def normalize_user_intent(plan: dict[str, Any], goal_override: str = "") -> dict[str, Any]:
    """Build the canonical Plan intent while retaining Diagnose provenance.

    An explicit Plan ``--goal`` describes what the bot should become. Diagnose
    intent describes how evidence was collected. They are deliberately stored
    separately so downstream document generation cannot confuse the two.
    """
    raw = plan.get("user_intent")
    if not isinstance(raw, dict):
        raw = {}
    pref = plan.get("case_preference") if isinstance(plan.get("case_preference"), dict) else {}
    source_raw_request = str(raw.get("raw_request") or pref.get("raw_message") or "").strip()
    source_intent = str(
        raw.get("intent_text") or pref.get("intent_text") or source_raw_request
    ).strip()
    explicit_goal = str(goal_override or "").strip()
    inherited_goal = _inherited_goal_text(plan)
    intent_text = explicit_goal or inherited_goal or source_intent
    source = (
        "cli_goal"
        if explicit_goal
        else "inherited_optimization_goal"
        if inherited_goal
        else "diagnose_intent"
        if source_intent
        else "default"
    )
    # Direct-goal analysis is a rewrite of this goal, not Diagnose evidence.
    has_diagnose_source = plan.get("input_mode") != "direct_goal"
    return {
        "schema_version": "clawevolve.plan-intent.v1",
        "raw_request": explicit_goal or inherited_goal or source_raw_request,
        "intent_text": intent_text,
        "goal_override": explicit_goal,
        "source": source,
        "confidence": 1.0 if explicit_goal else _number(raw.get("confidence", pref.get("intent_confidence", 0.0))),
        "optimization_objective": {
            "requested_outcome": intent_text,
            "explicit_goal": explicit_goal,
        },
        "source_diagnose_intent": source_intent if has_diagnose_source and source_intent != intent_text else "",
        "source_diagnose_raw_request": source_raw_request if has_diagnose_source and source_raw_request != intent_text else "",
        "requested_deliverables": _requested_deliverables(intent_text),
    }


def normalize_primary_metric(goal_text: str, default_target: Any = 0.90) -> dict[str, Any]:
    """Extract a measurable primary metric from a natural-language Plan goal."""
    text = str(goal_text or "").strip()
    for pattern in _PERCENT_METRIC_PATTERNS:
        match = pattern.search(text)
        if match:
            target = max(0.0, min(1.0, float(match.group("value")) / 100.0))
            return _metric_definition(match.group("label"), target, source="user_goal")
    try:
        target = max(0.0, min(1.0, float(default_target)))
    except (TypeError, ValueError):
        target = 0.90
    return {
        "name": "task_success_rate",
        "display_name": "任务成功率",
        "operator": ">=",
        "target": target,
        "unit": "ratio",
        "source": "default",
    }


def _metric_definition(label: str, target: float, *, source: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", "", str(label or "")).lower()
    if "mcp" in normalized:
        name = "mcp_call_success_rate"
        display_name = "MCP 调用成功率"
    elif "完成率" in normalized or "completionrate" in normalized:
        name = "task_completion_rate"
        display_name = "任务完成率"
    elif "任务" in normalized or normalized.startswith("task"):
        name = "task_success_rate"
        display_name = "任务成功率"
    else:
        name = "success_rate"
        display_name = "成功率"
    return {
        "name": name,
        "display_name": display_name,
        "operator": ">=",
        "target": target,
        "unit": "ratio",
        "source": source,
    }


def _inherited_goal_text(plan: dict[str, Any]) -> str:
    raw_goal = plan.get("default_optimization_goal")
    if isinstance(raw_goal, dict):
        value = str(raw_goal.get("goal_text") or "").strip()
        if value:
            return value
    elif str(raw_goal or "").strip():
        return str(raw_goal).strip()
    goal_context = plan.get("goal_context")
    if isinstance(goal_context, dict):
        return str(goal_context.get("raw_goal") or goal_context.get("goal_text") or "").strip()
    return ""


def _number(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _requested_deliverables(text: str) -> list[str]:
    lowered = text.lower()
    values: list[str] = []
    if any(x in lowered for x in ("skill", "技能")):
        values.append("skill capability or routing improvement")
    if any(x in lowered for x in ("%", "完成率", "success rate", "成功率")):
        values.append("measurable task completion improvement")
    return values
