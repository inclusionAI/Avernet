from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..models import RunResult



_ISSUE_DEFINITIONS: dict[str, dict[str, str]] = {
    "MISSING_TOOL_ARGUMENT": {
        "title": "工具调用参数缺失",
        "suggestion": "增加工具参数完整性检查和失败降级。",
    },
    "TOOL_CALL_NO_RETRY": {
        "title": "工具调用失败后未重试降级",
        "suggestion": "增加有限重试、错误归因和替代路径降级。",
    },
    "RETRIEVAL_NOT_CALLED": {
        "title": "应检索场景未检索",
        "suggestion": "增加时效性/外部事实识别，优先检索官方或权威来源。",
    },
    "WORKFLOW_PLANNING_FAILURE": {
        "title": "任务规划链路失败",
        "suggestion": "增加任务拆解、停止条件和阶段性结果校验。",
    },
    "OUTPUT_NOT_VERIFIED": {
        "title": "输出结果缺少验证",
        "suggestion": "输出前校验工具结果、任务目标和关键约束是否满足。",
    },
    "UNKNOWN_FAILURE_MODE": {
        "title": "未归类失败模式",
        "suggestion": "补充失败模式识别规则，并保留证据供人工复核。",
    },
}

_ISSUE_CODE_ALIASES: dict[str, str] = {
    "PARAMETER_ERROR": "MISSING_TOOL_ARGUMENT",
    "TOOL_PARAMETER_ERROR": "MISSING_TOOL_ARGUMENT",
    "MISSING_ARGUMENT": "MISSING_TOOL_ARGUMENT",
    "MISSING_REQUIRED_ARGUMENT": "MISSING_TOOL_ARGUMENT",
    "MISSING_TOOL_ARGUMENT": "MISSING_TOOL_ARGUMENT",
    "TOOL_EXECUTION_FAILURE": "TOOL_CALL_NO_RETRY",
    "TOOL_FAILURE": "TOOL_CALL_NO_RETRY",
    "TOOL_CALL_FAILED": "TOOL_CALL_NO_RETRY",
    "NO_RETRY": "TOOL_CALL_NO_RETRY",
    "TOOL_CALL_NO_RETRY": "TOOL_CALL_NO_RETRY",
    "RETRIEVAL_NOT_CALLED": "RETRIEVAL_NOT_CALLED",
    "SEARCH_NOT_CALLED": "RETRIEVAL_NOT_CALLED",
    "WORKFLOW_FAILURE": "WORKFLOW_PLANNING_FAILURE",
    "WORKFLOW_PLANNING_FAILURE": "WORKFLOW_PLANNING_FAILURE",
    "PLANNING_FAILURE": "WORKFLOW_PLANNING_FAILURE",
    "UNVERIFIED_OUTPUT": "OUTPUT_NOT_VERIFIED",
    "OUTPUT_NOT_VERIFIED": "OUTPUT_NOT_VERIFIED",
    "RESULT_NOT_VERIFIED": "OUTPUT_NOT_VERIFIED",
}

def build_execution_output(result: RunResult) -> dict[str, Any]:
    """Build the ClawWeb Diagnose Output object consumed by downstream Plan."""

    summary = result.summary or {}
    artifacts = summary.get("artifacts") or {}
    diagnose_result = _read_json(artifacts.get("diagnose_result_json", "")) or {}
    plan_source = _read_json(artifacts.get("plan_source_json", "")) or {}
    cases = [c for c in diagnose_result.get("cases") or [] if isinstance(c, dict)]
    if not cases and isinstance(plan_source, dict):
        cases = [c for c in plan_source.get("cases") or [] if isinstance(c, dict)]

    case_items = [_case_item(c) for c in cases]
    type_counter = Counter(item["type"] for item in case_items)
    total = len(case_items)
    bad_count = type_counter.get("bad", 0)
    good_count = type_counter.get("good", 0)

    return _strict_clawweb_diagnose_output(
        {
            "diagnosis": _diagnosis(summary, cases),
            "cases": {
                "total": total,
                "goodCount": good_count,
                "badCount": bad_count,
                "items": case_items,
            },
        }
    )


def build_success_summary(result: RunResult) -> str:
    """Build the top-level ClawWeb step summary for succeeded diagnose reports."""

    return "完成近期 Case 抽取与问题诊断"


def _diagnosis(summary: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    issues = _issues(summary, cases)
    top = issues[0] if issues else {}
    text = top.get("title") or "暂无高频问题"
    return {
        "summary": f"{text}是当前主要问题" if issues else "未发现可上报的主要问题",
        "issues": issues,
    }


def _issues(summary: dict[str, Any], cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not cases:
        return []
    bad_cases = [c for c in cases if _case_type(c) == "bad"]
    source_cases = bad_cases or cases
    counter: Counter[str] = Counter()
    examples: dict[str, dict[str, Any]] = {}
    for case in source_cases:
        code = _issue_code(case)
        counter[code] += 1
        examples.setdefault(code, case)
    total_bad = max(1, len(bad_cases))
    issues: list[dict[str, Any]] = []
    for code, count in counter.most_common(5):
        sample = examples[code]
        title = _issue_title(sample, code)
        issues.append(
            {
                "code": code,
                "title": title,
                "severity": "high" if count / total_bad >= 0.5 else "medium",
                "caseCount": count,
                "suggestion": _suggestion(summary, sample),
            }
        )
    return issues


def _case_item(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "caseId": str(case.get("case_id") or case.get("caseId") or case.get("session_id") or ""),
        "type": _case_type(case),
        "summary": _case_summary(case),
    }


def _case_type(case: dict[str, Any]) -> str:
    value = str(case.get("case_type") or case.get("type") or "bad").strip().lower()
    return "good" if value == "good" else "bad"


def _case_summary(case: dict[str, Any]) -> str:
    analysis = case.get("analysis") if isinstance(case.get("analysis"), dict) else {}
    text = (
        analysis.get("root_cause_summary")
        or (analysis.get("selection") or {}).get("reason")
        or case.get("root_cause_summary")
        or case.get("selection_reason")
        or case.get("query")
        or case.get("summary")
        or ""
    )
    text = " ".join(str(text).split())
    return text[:180]


def _issue_code(case: dict[str, Any]) -> str:
    analysis = case.get("analysis") if isinstance(case.get("analysis"), dict) else {}
    raw_values = [
        analysis.get("evolution_failure_mode"),
        analysis.get("root_cause_class"),
        analysis.get("common_problem_key"),
        case.get("evolution_failure_mode"),
        case.get("root_cause_class"),
        case.get("common_problem_key"),
    ]
    for raw in raw_values:
        normalized = _normalize_issue_token(raw)
        if normalized in _ISSUE_CODE_ALIASES:
            return _ISSUE_CODE_ALIASES[normalized]
    return "UNKNOWN_FAILURE_MODE"


def _normalize_issue_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _issue_title(case: dict[str, Any], code: str) -> str:
    defined = _ISSUE_DEFINITIONS.get(code) or {}
    if defined.get("title"):
        return defined["title"]
    analysis = case.get("analysis") if isinstance(case.get("analysis"), dict) else {}
    return str(
        analysis.get("common_problem_key")
        or analysis.get("root_cause_class")
        or case.get("common_problem_key")
        or case.get("root_cause_class")
        or code.replace("_", " ").title()
    )[:80]


def _suggestion(summary: dict[str, Any], case: dict[str, Any]) -> str:
    code = _issue_code(case)
    defined = _ISSUE_DEFINITIONS.get(code) or {}
    if defined.get("suggestion"):
        return defined["suggestion"]
    planning = case.get("planning_hints") if isinstance(case.get("planning_hints"), dict) else {}
    hints = planning.get("tool_hints") or case.get("tool_hints") or []
    if hints:
        return "优先优化：" + "、".join(str(h) for h in hints[:3])
    goals = summary.get("selection_report", {}).get("optimization_goals") or []
    if goals:
        return str(goals[0])[:160]
    return _ISSUE_DEFINITIONS["UNKNOWN_FAILURE_MODE"]["suggestion"]


def _top_issue(summary: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    issues = _issues(summary, cases)
    return issues[0] if issues else {}


def _strict_clawweb_diagnose_output(output: dict[str, Any]) -> dict[str, Any]:
    """Return exactly the ClawWeb diagnose output schema requested by product.

    Allowed structure only:
    {
      "diagnosis": {"summary": str, "issues": [{"code", "title", "severity", "caseCount", "suggestion"}]},
      "cases": {"total": int, "goodCount": int, "badCount": int, "items": [{"caseId", "type", "summary"}]}
    }
    """

    diagnosis = output.get("diagnosis") if isinstance(output.get("diagnosis"), dict) else {}
    cases = output.get("cases") if isinstance(output.get("cases"), dict) else {}
    strict_issues: list[dict[str, Any]] = []
    for issue in diagnosis.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        strict_issues.append(
            {
                "code": str(issue.get("code") or "UNKNOWN_FAILURE_MODE"),
                "title": str(issue.get("title") or "未归类失败模式"),
                "severity": str(issue.get("severity") or "medium"),
                "caseCount": int(issue.get("caseCount") or 0),
                "suggestion": str(issue.get("suggestion") or "补充失败模式识别规则，并保留证据供人工复核。"),
            }
        )
    strict_items: list[dict[str, Any]] = []
    for item in cases.get("items") or []:
        if not isinstance(item, dict):
            continue
        strict_items.append(
            {
                "caseId": str(item.get("caseId") or ""),
                "type": "good" if str(item.get("type") or "").lower() == "good" else "bad",
                "summary": str(item.get("summary") or ""),
            }
        )
    return {
        "diagnosis": {
            "summary": str(diagnosis.get("summary") or "未发现可上报的主要问题"),
            "issues": strict_issues,
        },
        "cases": {
            "total": int(cases.get("total") or len(strict_items)),
            "goodCount": int(cases.get("goodCount") or sum(1 for item in strict_items if item["type"] == "good")),
            "badCount": int(cases.get("badCount") or sum(1 for item in strict_items if item["type"] == "bad")),
            "items": strict_items,
        },
    }


def _read_json(path_value: str) -> Any:
    try:
        path = Path(str(path_value or "")).expanduser()
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
