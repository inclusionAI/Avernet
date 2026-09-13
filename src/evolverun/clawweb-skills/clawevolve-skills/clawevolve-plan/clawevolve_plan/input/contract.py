from __future__ import annotations

import hashlib
import json
import math
from typing import Any


PLAN_SOURCE_SCHEMA_VERSION = "plan-source/v2"
PLAN_SOURCE_DESCRIPTOR_VERSION = "plan-source-descriptor/v2"


class PlanSourceError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable

    def report_error(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "stage": self.stage,
            "retryable": self.retryable,
        }


def _canonical_number(value: int | float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            "Plan Source 不能包含非有限数字",
            stage="schema_validation",
        )
    if number == 0:
        return "0"
    mantissa, exponent = format(number, ".16e").split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    return f"{mantissa}e{int(exponent)}"


def canonical_json(value: Any) -> str:
    """Cross-runtime canonical JSON shared with ClawWeb's TypeScript module."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: item[0])
        return "{" + ",".join(
            json.dumps(str(key), ensure_ascii=False, separators=(",", ":"))
            + ":"
            + canonical_json(child)
            for key, child in items
        ) + "}"
    raise PlanSourceError(
        "PLAN_SOURCE_SCHEMA_INVALID",
        f"Plan Source 不能包含 {type(value).__name__}",
        stage="schema_validation",
    )


def digest_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _record(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            f"{path} 必须是对象",
            stage="schema_validation",
        )
    return value


def _required_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            f"{path} 必须是非空字符串",
            stage="schema_validation",
        )
    return value


def validate_plan_source(value: Any) -> dict[str, Any]:
    document = _record(value, "Plan Source")
    if document.get("schema_version") != PLAN_SOURCE_SCHEMA_VERSION:
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            f"schema_version 不支持: {document.get('schema_version', '')}",
            stage="schema_validation",
        )
    _required_string(document.get("generated_at"), "generated_at")
    source = _record(document.get("source"), "source")
    if source.get("type") not in {"diagnose", "insight_improvement", "direct_goal"}:
        raise PlanSourceError("PLAN_SOURCE_SCHEMA_INVALID", "source.type 不支持", stage="schema_validation")
    for key in ("id", "producer", "bot_id", "version"):
        _required_string(source.get(key), f"source.{key}")
    problem = _record(document.get("problem"), "problem")
    _required_string(problem.get("title"), "problem.title")
    if problem.get("user_guidance") is not None and not isinstance(problem.get("user_guidance"), str):
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            "problem.user_guidance 必须是字符串或 null",
            stage="schema_validation",
        )
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise PlanSourceError("PLAN_SOURCE_SCHEMA_INVALID", "cases 必须是非空数组", stage="schema_validation")
    case_ids: set[str] = set()
    for index, raw_case in enumerate(cases):
        case = _record(raw_case, f"cases[{index}]")
        case_id = _required_string(case.get("case_id"), f"cases[{index}].case_id")
        if case_id in case_ids:
            raise PlanSourceError(
                "PLAN_SOURCE_SCHEMA_INVALID",
                f"cases[{index}].case_id 重复",
                stage="schema_validation",
            )
        case_ids.add(case_id)
        case_type = case.get("case_type")
        if case_type not in {"good", "bad", "prospective"}:
            raise PlanSourceError(
                "PLAN_SOURCE_SCHEMA_INVALID",
                f"cases[{index}].case_type 不支持",
                stage="schema_validation",
            )
        if case_type != "prospective":
            _required_string(case.get("session_id"), f"cases[{index}].session_id")
        elif case.get("session_id") not in {None, ""}:
            raise PlanSourceError(
                "PLAN_SOURCE_SCHEMA_INVALID",
                f"cases[{index}].session_id must be absent for prospective cases",
                stage="schema_validation",
            )
        _required_string(case.get("query"), f"cases[{index}].query")
        _record(case.get("evidence"), f"cases[{index}].evidence")
    analysis = _record(document.get("analysis"), "analysis")
    _record(analysis.get("case_distribution"), "analysis.case_distribution")
    if not isinstance(analysis.get("root_cause_clusters"), list):
        raise PlanSourceError(
            "PLAN_SOURCE_SCHEMA_INVALID",
            "analysis.root_cause_clusters 必须是数组",
            stage="schema_validation",
        )
    _record(document.get("planning_hints"), "planning_hints")
    _record(document.get("extensions"), "extensions")
    return document
