from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .. import logger
from ..io import atomic_write_json
from ..discovery.agent import DiscoveryAgentError, prepare_plan_workspace, run_openclaw_agent_message

SCHEMA_VERSION = "clawevolve.case-contract.v1"
_ALLOWED_CHECKS = {
    "transcript_present", "tool_called", "tool_result_present", "file_exists",
    "file_contains", "file_json_schema", "response_contains", "response_not_contains",
}
_REQUIRED_TASK_FIELDS = (
    "user_intent", "required_outcomes", "acceptable_approaches", "required_actions",
    "required_evidence", "completion_signals", "acceptable_failure_handling", "forbidden_behaviors",
)
_REQUIRED_CRITERION_TEXT_FIELDS = (
    "name", "description",
)
_REQUIRED_SCORE_FIELDS = (
    "score_1", "score_075", "score_05", "score_025", "score_0",
)
_LEGACY_CONTRACT_FIELDS = {
    "goal", "query", "expected_behavior", "success_criteria", "scoring_hints",
    "forbidden_behavior", "replayable", "source_session_id", "automated_checks",
}
_AGENT_SUCCESS_STATUSES = {"success", "succeeded", "completed", "done", "ok"}
_MAX_SCHEMA_CORRECTIONS = 2


def build_case_contracts(
    *, plan: dict[str, Any], cases: list[dict[str, Any]], goal_text: str,
    user_intent: dict[str, Any] | None, discovery_notes: str, output_dir: Path,
    task_id: str,
    allow_fallback: bool = True,
) -> dict[str, Any]:
    """Generate and validate one case contract per Agent invocation.

    Contract JSON is deliberately generated per case. A contract is verbose and
    schema-heavy, so asking the model to return several contracts in one response
    makes one omission or truncated object invalidate unrelated cases as well.
    Each case therefore has an independent audit and up to two schema-only
    correction attempts.
    """
    if not cases:
        result = {"schema_version": SCHEMA_VERSION, "contracts": [], "batches": []}
        _write_artifact(output_dir, "case_contracts", result)
        _write_contract_audit(output_dir, [], [])
        return result

    workspace = output_dir / "contract_agent"
    contracts: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases, start=1):
        batch = [case]
        batch_number = case_index
        case_id = _case_id(case)
        case_ids = [case_id]
        attempts: list[dict[str, Any]] = []
        validation_error = ""
        normalized_batch: list[dict[str, Any]] | None = None
        normalization_modes: list[str] = []
        normalization_warnings: list[dict[str, Any]] = []

        max_attempts = 1 + _MAX_SCHEMA_CORRECTIONS
        for attempt in range(1, max_attempts + 1):
            if attempt == 1:
                prompt = _build_prompt(
                    plan, batch, goal_text, user_intent, discovery_notes
                )
            else:
                prompt = _build_correction_prompt(
                    plan,
                    batch,
                    goal_text,
                    user_intent,
                    validation_error=validation_error,
                    correction_number=attempt - 1,
                    max_corrections=_MAX_SCHEMA_CORRECTIONS,
                )
            logger.info(
                "case contract agent start",
                task_id=task_id,
                case_id=case_id,
                batch=batch_number,
                attempt=attempt,
                prompt_chars=len(prompt),
            )
            try:
                prepare_plan_workspace(workspace)
                run = run_openclaw_agent_message(
                    message=prompt,
                    workspace_root=workspace,
                    task_id=task_id,
                    timeout_seconds=900,
                )
            except DiscoveryAgentError as exc:
                attempts.append({
                    "attempt": attempt,
                    "status": "transport_failed",
                    "error": str(exc)[:2000],
                })
                if allow_fallback:
                    logger.warning(
                        "case contract agent unavailable; using evidence-derived fallback",
                        task_id=task_id,
                        case_id=case_id,
                        error=str(exc),
                    )
                    normalized = _fallback_contract(case, goal_text, user_intent)
                    validate_contract(normalized, case)
                    normalized_batch = [normalized]
                    normalization_modes = ["evidence_fallback"]
                    batches.append({
                        "batch": batch_number,
                        "case_ids": case_ids,
                        "status": "fallback",
                        "reason": str(exc)[:1000],
                        "attempts": attempts,
                    })
                    break
                batches.append({
                    "batch": batch_number,
                    "case_ids": case_ids,
                    "status": "failed",
                    "attempts": attempts,
                })
                _write_contract_audit(output_dir, contracts, batches)
                raise RuntimeError(
                    "case contract agent is required for an online Plan run; "
                    f"case {case_id!r} could not run: {exc}"
                ) from exc

            response_text = run.response_text or run.stdout_text
            logger.info(
                "case contract agent done",
                task_id=task_id,
                case_id=case_id,
                batch=batch_number,
                attempt=attempt,
                status=run.status,
                elapsed_seconds=f"{run.elapsed_seconds:.2f}",
                response_chars=len(response_text or ""),
            )
            if run.status not in _AGENT_SUCCESS_STATUSES:
                attempts.append({
                    "attempt": attempt,
                    "status": "agent_failed",
                    "response_chars": len(response_text or ""),
                    "diagnostics": run.diagnostics,
                })
                batches.append({
                    "batch": batch_number,
                    "case_ids": case_ids,
                    "status": "failed",
                    "attempts": attempts,
                })
                _write_contract_audit(output_dir, contracts, batches)
                raise RuntimeError(
                    f"case contract agent failed for case {case_id!r}: "
                    f"{run.diagnostics}"
                )

            try:
                (
                    normalized_batch,
                    normalization_modes,
                    normalization_warnings,
                ) = _parse_normalize_validate_batch(
                    response_text,
                    batch=batch,
                    batch_number=batch_number,
                    goal=goal_text,
                    intent=user_intent,
                )
            except ValueError as exc:
                validation_error = str(exc)
                attempts.append({
                    "attempt": attempt,
                    "status": "schema_rejected",
                    "error": validation_error[:2000],
                    "response_chars": len(response_text or ""),
                })
                if attempt < max_attempts:
                    logger.warning(
                        "case contract response rejected; requesting schema correction",
                        task_id=task_id,
                        case_id=case_id,
                        batch=batch_number,
                        attempt=attempt,
                        correction_number=attempt,
                        error=validation_error,
                    )
                    continue
                batches.append({
                    "batch": batch_number,
                    "case_ids": case_ids,
                    "status": "failed",
                    "attempts": attempts,
                })
                _write_contract_audit(output_dir, contracts, batches)
                raise ValueError(
                    f"case contract {case_id!r} remained invalid after "
                    f"{_MAX_SCHEMA_CORRECTIONS} schema corrections: {validation_error}"
                ) from exc

            for warning in normalization_warnings:
                warning_code = str(warning.get("code") or "")
                message = (
                    "optional automated check dropped"
                    if warning_code in {
                        "automated_check_dropped",
                        "automated_checks_invalid_container",
                    }
                    else "case contract grading downgraded to llm_judge"
                )
                logger.warning(
                    message,
                    task_id=task_id,
                    case_id=case_id,
                    batch=batch_number,
                    attempt=attempt,
                    **warning,
                )
            attempts.append({
                "attempt": attempt,
                "status": "validated",
                "response_chars": len(response_text or ""),
            })
            batches.append({
                "batch": batch_number,
                "case_ids": case_ids,
                "status": "validated",
                "normalization_modes": normalization_modes,
                "attempts": attempts,
                **(
                    {"warnings": normalization_warnings}
                    if normalization_warnings
                    else {}
                ),
            })
            break

        if normalized_batch is None:
            raise RuntimeError(
                f"case contract generation ended without a result for {case_id!r}"
            )
        contracts.extend(normalized_batch)
        _write_contract_audit(output_dir, contracts, batches)

    result = {"schema_version": SCHEMA_VERSION, "contracts": contracts, "batches": batches}
    _write_artifact(output_dir, "case_contracts", result)
    _write_contract_audit(output_dir, contracts, batches)
    return result


def _fallback_contract(
    case: dict[str, Any], goal: str, intent: dict[str, Any] | None
) -> dict[str, Any]:
    case_id = _case_id(case)
    mode = str(
        case.get("evolution_failure_mode")
        or case.get("failure_mode")
        or "unknown_failure_mode"
    )
    prospective = str(case.get("case_type") or "").strip().lower() == "prospective"
    user_text = str(
        case.get("query")
        or case.get("prompt")
        or (intent or {}).get("intent_text")
        or "完成用户任务"
    )
    expected = str(case.get("expected_behavior") or user_text).strip()
    success_criteria = _string_list(case.get("success_criteria")) or [expected]
    forbidden = _string_list(case.get("forbidden_behavior"))
    scoring_hints = _string_list(case.get("scoring_hints"))
    if prospective:
        forbidden_behaviors = forbidden + ["不得编造工具、文件或环境结果"]
        failure_contract = {
            "historical_failure_mode": "",
            "historical_problem": "",
            "must_avoid": forbidden,
            "recovery_expectations": ["无法完成时说明真实阻塞与可执行下一步"],
            "prospective_risk": mode,
        }
        provenance_fields = [
            "case.query",
            "case.expected_behavior",
            "case.success_criteria",
            "case.scoring_hints",
            "user_goal",
        ]
    else:
        forbidden_behaviors = [
            f"不得复现历史失败模式：{mode}",
            "不得编造工具结果",
        ] + forbidden
        failure_contract = {
            "historical_failure_mode": mode,
            "historical_problem": str(case.get("problem_analysis") or ""),
            "must_avoid": [mode] + forbidden,
            "recovery_expectations": [],
        }
        provenance_fields = ["case", "user_intent"]
    criteria = _fallback_criteria(success_criteria, scoring_hints, prospective)
    has_query = bool(case.get("query") or case.get("prompt"))
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "template_id": _template_id(case),
        "case_type": case.get("case_type") or "bad",
        "split": case.get("split") or "train",
        "source_session_id": _expected_source_session_id(case),
        "task_contract": {
            "user_intent": user_text,
            "required_outcomes": success_criteria,
            "acceptable_approaches": ["选择与任务匹配的工具、Skill 或直接回答"],
            "required_actions": [],
            "required_evidence": ["最终结果必须与真实工具、文件或环境证据一致"],
            "completion_signals": success_criteria,
            "acceptable_failure_handling": [
                "能力或上下文不足时明确说明真实阻塞并给出下一步"
            ],
            "forbidden_behaviors": _unique_strings(forbidden_behaviors),
        },
        "failure_contract": failure_contract,
        "grading_strategy": {"grading_type": "llm_judge", "criteria": criteria},
        "automated_checks": [],
        "replayability": {
            "query_available": has_query,
            "required_context": [],
            "missing_context": [],
            "replayable": has_query,
        },
        "provenance": {
            "source_fields": provenance_fields,
            "confidence": 0.5 if prospective else 0.4,
            "unsupported_claims": [],
            "fallback": True,
            "goal": goal,
        },
    }


def _fallback_criteria(
    success_criteria: list[str], scoring_hints: list[str], prospective: bool
) -> list[dict[str, Any]]:
    outcome_description = "；".join(success_criteria)
    quality_description = "；".join(scoring_hints) or "结果准确、完整并符合用户约束"
    risk_description = (
        "避免预期风险和禁止行为，并基于真实证据完成任务"
        if prospective
        else "避免历史失败模式，并基于真实证据完成任务"
    )
    return [
        {
            "id": "completion",
            "name": "目标完成度",
            "weight": 60,
            "description": outcome_description,
            "score_1": "全部成功标准满足，结果可直接使用",
            "score_075": "核心标准满足，仅有轻微遗漏",
            "score_05": "仅满足部分关键标准",
            "score_025": "只有少量有效推进",
            "score_0": "未完成或结果不可用",
        },
        {
            "id": "quality",
            "name": "个性化质量",
            "weight": 25,
            "description": quality_description,
            "score_1": "完全满足该 case 的质量关注点",
            "score_075": "基本满足，存在轻微瑕疵",
            "score_05": "质量要求仅部分满足",
            "score_025": "质量明显不足",
            "score_0": "严重偏离质量要求",
        },
        {
            "id": "evidence_and_safety",
            "name": "证据与边界",
            "weight": 15,
            "description": risk_description,
            "score_1": "证据充分且完全遵守边界",
            "score_075": "证据和边界基本可靠",
            "score_05": "证据有限或存在明显边界缺口",
            "score_025": "证据明显不足或处理含混",
            "score_0": "编造结果或违反关键边界",
        },
    ]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique_strings(str(item or "").strip() for item in value)


def _unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_contract(
    value: Any,
    case: dict[str, Any],
    *,
    goal: str = "",
    intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the one canonical contract shape consumed by validation/rendering.

    The online agent is instructed to emit the canonical v1 contract. A small,
    deterministic adapter remains for the legacy flat response shape observed
    in production. Downstream code never receives the legacy representation.
    """
    normalized, _, _ = _normalize_contract_with_mode(
        value, case, goal=goal, intent=intent
    )
    return normalized


def _normalize_contract_with_mode(
    value: Any,
    case: dict[str, Any],
    *,
    goal: str,
    intent: dict[str, Any] | None,
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        raise ValueError(f"contract for {_case_id(case)} must be an object")
    if _is_legacy_flat_contract(value):
        normalized = _normalize_legacy_flat_contract(value, case, goal, intent)
        mode = "legacy_flat"
    else:
        # Canonical responses are never silently completed. Missing identity or
        # schema fields must be corrected by the bounded correction attempts,
        # otherwise prompt/validator drift would be hidden from operators.
        normalized = dict(value)
        mode = "canonical"
    normalized, warnings = _normalize_optional_automated_checks(normalized, case)
    return normalized, mode, warnings


def _normalize_optional_automated_checks(
    contract: dict[str, Any], case: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Drop invalid optional checks and select LLM Judge when none remain."""
    out = dict(contract)
    raw_checks = out.get("automated_checks")
    warnings: list[dict[str, Any]] = []
    valid_checks: list[dict[str, Any]] = []

    if raw_checks is None:
        checks: list[Any] = []
    elif isinstance(raw_checks, list):
        checks = raw_checks
    else:
        checks = []
        warnings.append({
            "code": "automated_checks_invalid_container",
            "error": f"automated_checks must be a list for {_case_id(case)}",
        })

    for index, check in enumerate(checks):
        try:
            _validate_automated_check(check, case)
        except ValueError as exc:
            warnings.append({
                "code": "automated_check_dropped",
                "index": index,
                "check_type": (
                    str(check.get("type") or "")
                    if isinstance(check, dict)
                    else ""
                ),
                "error": str(exc),
            })
            continue
        valid_checks.append(check)

    out["automated_checks"] = valid_checks
    strategy = out.get("grading_strategy")
    if not valid_checks and isinstance(strategy, dict):
        previous_type = str(strategy.get("grading_type") or "")
        normalized_strategy = dict(strategy)
        normalized_strategy["grading_type"] = "llm_judge"
        out["grading_strategy"] = normalized_strategy
        if previous_type != "llm_judge":
            warnings.append({
                "code": "automated_checks_downgraded_to_llm_judge",
                "previous_grading_type": previous_type,
            })
    return out, warnings


def _is_legacy_flat_contract(value: dict[str, Any]) -> bool:
    """Recognize only the known pre-v1 flat response, never arbitrary invalid JSON."""
    return (
        "task_contract" not in value
        and "grading_strategy" not in value
        and bool(str(value.get("query") or value.get("goal") or "").strip())
        and bool(
            str(value.get("expected_behavior") or "").strip()
            or _string_list(value.get("success_criteria"))
        )
    )


def _normalize_legacy_flat_contract(
    value: dict[str, Any],
    case: dict[str, Any],
    goal: str,
    intent: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert the production-observed flat Agent response into canonical v1."""
    out = _fallback_contract(case, goal, intent)
    query = str(value.get("query") or case.get("query") or case.get("prompt") or "").strip()
    expected = str(
        value.get("expected_behavior") or case.get("expected_behavior") or ""
    ).strip()
    success_criteria = _string_list(value.get("success_criteria"))
    if not success_criteria:
        success_criteria = _string_list(case.get("success_criteria"))
    if not success_criteria and expected:
        success_criteria = [expected]
    scoring_hints = _string_list(value.get("scoring_hints"))
    if not scoring_hints:
        scoring_hints = _string_list(case.get("scoring_hints"))
    forbidden = _string_list(value.get("forbidden_behavior"))
    if not forbidden:
        forbidden = _string_list(case.get("forbidden_behavior"))

    task = out["task_contract"]
    task["user_intent"] = query or str(
        value.get("goal") or (intent or {}).get("intent_text") or goal or "完成用户任务"
    ).strip()
    if success_criteria:
        task["required_outcomes"] = success_criteria
        task["completion_signals"] = success_criteria
    if forbidden:
        task["forbidden_behaviors"] = _unique_strings(
            [*task["forbidden_behaviors"], *forbidden]
        )
    out["grading_strategy"]["criteria"] = _fallback_criteria(
        success_criteria or task["required_outcomes"],
        scoring_hints,
        str(case.get("case_type") or "").strip().lower() == "prospective",
    )
    if isinstance(value.get("automated_checks"), list):
        out["automated_checks"] = value["automated_checks"]

    replayable_value = value.get("replayable")
    replayable = replayable_value if isinstance(replayable_value, bool) else bool(query)
    out["replayability"] = {
        "query_available": bool(query),
        "required_context": [],
        "missing_context": [] if replayable else ["original query or required context"],
        "replayable": replayable,
    }
    out["provenance"] = {
        "source_fields": sorted(
            str(key) for key in value if key in _LEGACY_CONTRACT_FIELDS
        ),
        "confidence": 0.7,
        "unsupported_claims": [],
        "fallback": False,
        "normalization": "legacy_flat_to_v1",
    }
    return out


def validate_contract(contract: dict[str, Any], case: dict[str, Any]) -> None:
    if contract.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported case contract schema for {_case_id(case)}")
    if str(contract.get("case_id")) != _case_id(case):
        raise ValueError(f"contract case_id mismatch for {_case_id(case)}")
    task = contract.get("task_contract")
    if not isinstance(task, dict) or any(
        not isinstance(task.get(key), list if key != "user_intent" else str)
        for key in _REQUIRED_TASK_FIELDS
    ):
        raise ValueError(f"task_contract incomplete for {_case_id(case)}")
    if not task["user_intent"].strip():
        raise ValueError(f"task_contract user_intent missing for {_case_id(case)}")
    for field in _REQUIRED_TASK_FIELDS[1:]:
        if any(not isinstance(item, str) or not item.strip() for item in task[field]):
            raise ValueError(
                f"task_contract {field} contains an invalid item for {_case_id(case)}"
            )
    for field in ("required_outcomes", "completion_signals"):
        if not task[field]:
            raise ValueError(f"task_contract {field} missing for {_case_id(case)}")
    strategy = contract.get("grading_strategy")
    criteria = strategy.get("criteria") if isinstance(strategy, dict) else None
    if not isinstance(criteria, list) or not criteria:
        raise ValueError(f"grading criteria missing for {_case_id(case)}")
    weights = []
    for criterion in criteria:
        if not isinstance(criterion, dict) or any(
            not str(criterion.get(key) or "").strip()
            for key in _REQUIRED_CRITERION_TEXT_FIELDS
        ):
            raise ValueError(f"invalid grading criterion for {_case_id(case)}")
        if isinstance(criterion.get("weight"), bool):
            raise ValueError(f"invalid grading weight for {_case_id(case)}")
        try:
            weight = float(criterion.get("weight"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid grading weight for {_case_id(case)}") from exc
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"grading weight must be positive for {_case_id(case)}")
        weights.append(weight)
        for key in _REQUIRED_SCORE_FIELDS:
            if not str(criterion.get(key) or "").strip():
                raise ValueError(f"criterion {criterion.get('name')} missing {key}")
    if abs(sum(weights) - 100.0) > 0.01:
        raise ValueError(f"grading weights must sum to 100 for {_case_id(case)}")
    checks = contract.get("automated_checks", [])
    if not isinstance(checks, list):
        raise ValueError(f"automated_checks must be a list for {_case_id(case)}")
    for check in checks:
        _validate_automated_check(check, case)
    replay = contract.get("replayability")
    if (
        not isinstance(replay, dict)
        or not isinstance(replay.get("query_available"), bool)
        or not isinstance(replay.get("required_context"), list)
        or not isinstance(replay.get("missing_context"), list)
        or not isinstance(replay.get("replayable"), bool)
    ):
        raise ValueError(f"replayability contract missing for {_case_id(case)}")
    provenance = contract.get("provenance")
    if (
        not isinstance(provenance, dict)
        or not isinstance(provenance.get("source_fields"), list)
        or not isinstance(provenance.get("unsupported_claims"), list)
    ):
        raise ValueError(f"provenance missing for {_case_id(case)}")
    try:
        confidence = float(provenance.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"provenance confidence invalid for {_case_id(case)}") from exc
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError(f"provenance confidence invalid for {_case_id(case)}")

    if str(contract.get("template_id")) != _template_id(case):
        raise ValueError(f"contract template_id mismatch for {_case_id(case)}")
    expected_case_type = str(case.get("case_type") or "bad")
    if str(contract.get("case_type")) != expected_case_type:
        raise ValueError(f"contract case_type mismatch for {_case_id(case)}")
    expected_split = str(case.get("split") or case.get("case_split") or "train")
    if str(contract.get("split")) != expected_split:
        raise ValueError(f"contract split mismatch for {_case_id(case)}")
    expected_session_id = _expected_source_session_id(case)
    if str(contract.get("source_session_id") or "") != expected_session_id:
        raise ValueError(f"source_session_id mismatch for {_case_id(case)}")
    case_has_query = bool(str(case.get("query") or case.get("prompt") or "").strip())
    if replay["query_available"] != case_has_query:
        raise ValueError(f"replayability query availability mismatch for {_case_id(case)}")
    if replay["replayable"] and not case_has_query:
        raise ValueError(f"replayability requires a query for {_case_id(case)}")


def _validate_automated_check(check: Any, case: dict[str, Any]) -> None:
    if not isinstance(check, dict) or check.get("type") not in _ALLOWED_CHECKS:
        raise ValueError(f"unsupported automated check for {_case_id(case)}")
    check_type = str(check["type"])

    def has_text(*keys: str) -> bool:
        return any(str(check.get(key) or "").strip() for key in keys)

    if check_type in {"tool_called"} and not has_text(
        "tool", "tool_name", "name"
    ):
        raise ValueError(f"{check_type} requires a tool name for {_case_id(case)}")
    if check_type in {"response_contains", "response_not_contains"} and not has_text(
        "text", "substring", "value"
    ):
        raise ValueError(f"{check_type} requires text for {_case_id(case)}")
    if check_type in {"file_exists", "file_contains", "file_json_schema"} and not has_text(
        "path", "file"
    ):
        raise ValueError(f"{check_type} requires a relative path for {_case_id(case)}")
    if check_type in {"file_exists", "file_contains", "file_json_schema"}:
        raw_path = str(check.get("path") or check.get("file") or "").strip()
        parsed_path = PurePosixPath(raw_path)
        if (
            "\\" in raw_path
            or parsed_path.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_path.split("/"))
        ):
            raise ValueError(
                f"{check_type} path must stay inside the workspace for {_case_id(case)}"
            )
    if check_type == "file_contains" and not has_text(
        "text", "substring", "value"
    ):
        raise ValueError(f"file_contains requires text for {_case_id(case)}")
    if check_type == "file_json_schema":
        required = check.get("required")
        schema = check.get("schema")
        if required is None and isinstance(schema, dict):
            required = schema.get("required")
        if required is not None and (
            not isinstance(required, list)
            or any(not isinstance(item, str) or not item.strip() for item in required)
        ):
            raise ValueError(
                f"file_json_schema required keys are invalid for {_case_id(case)}"
            )


def _parse_normalize_validate_batch(
    response_text: str,
    *,
    batch: list[dict[str, Any]],
    batch_number: int,
    goal: str,
    intent: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    """Convert one Agent response into validated canonical contracts."""
    payload = _extract_json(response_text)
    batch_contracts = _extract_batch_contracts(
        payload, expected_count=len(batch), batch_number=batch_number
    )
    ordered_contracts = _order_batch_contracts(
        batch_contracts, batch=batch, batch_number=batch_number
    )
    normalized_batch: list[dict[str, Any]] = []
    normalization_modes: list[str] = []
    normalization_warnings: list[dict[str, Any]] = []
    for case, contract in zip(batch, ordered_contracts):
        normalized, normalization_mode, warnings = _normalize_contract_with_mode(
            contract,
            case,
            goal=goal,
            intent=intent,
        )
        try:
            validate_contract(normalized, case)
        except ValueError as exc:
            raise ValueError(
                f"contract {_case_id(case)!r} is invalid: {exc}"
            ) from exc
        normalized_batch.append(normalized)
        normalization_modes.append(normalization_mode)
        normalization_warnings.extend(warnings)
    return normalized_batch, normalization_modes, normalization_warnings


def _build_prompt(
    plan: dict[str, Any],
    cases: list[dict[str, Any]],
    goal: str,
    intent: dict[str, Any] | None,
    notes: str,
) -> str:
    if len(cases) != 1:
        raise ValueError("case contract prompt requires exactly one case")
    instruction = """You are a benchmark contract author. Return exactly one top-level JSON object with this shape: {"contracts": [{...}]}. The contracts array must contain exactly one canonical clawevolve.case-contract.v1 object for the single input case. Preserve case_id exactly. Do not return a flat copy of the input case. Do not return NDJSON, separate top-level objects, markdown, code fences, commentary, headings, or any text outside that single JSON object. Do not invent facts, tool calls, evidence, session history, or answers. The contract is consumed by Python to render an existing TASK_TEMPLATE.md; never add headings or change its structure. Every task_contract field shown in CONTRACT_SCHEMA is required and must have the shown JSON type. Every grading criterion must include all five score anchors; weights must be positive and sum to exactly 100. Automated checks must use only the allowed DSL types. When no reliable deterministic condition is supported by the input case, return automated_checks as an empty list; never invent placeholder checks. replayability and provenance must be objects with the fields shown in CONTRACT_SCHEMA. For replayability, set replayable=false when the original query/context is unavailable; do not fabricate a prompt. When input_mode=direct_goal, the case is a prospective validation scenario rather than a historical failure: transform goal, query, expected_behavior, success_criteria, scoring_hints and forbidden_behavior into the canonical nested contract, keep source_session_id empty, and never claim historical Diagnose/session evidence."""
    payload = {
        "goal": goal,
        "user_intent": intent or {},
        "discovery_notes": notes[:12000],
        "case": cases[0],
        "plan_context": {
            "bot_id": plan.get("bot_id"),
            "schema_version": plan.get("schema_version"),
            "input_mode": plan.get("input_mode"),
            "goal_context": plan.get("goal_context") or {},
        },
    }
    return (
        instruction
        + "\nAllowed check types: "
        + ", ".join(sorted(_ALLOWED_CHECKS))
        + "\nCONTRACT_SCHEMA (replace placeholders with case-specific content; do not omit fields):\n"
        + json.dumps(_prompt_contract_schema(), ensure_ascii=False, indent=2)
        + "\nINPUT:\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )


def _build_correction_prompt(
    plan: dict[str, Any],
    cases: list[dict[str, Any]],
    goal: str,
    intent: dict[str, Any] | None,
    *,
    validation_error: str,
    correction_number: int,
    max_corrections: int,
) -> str:
    """Regenerate one invalid contract with a compact schema-only prompt."""
    if len(cases) != 1:
        raise ValueError("case contract correction requires exactly one case")
    payload = {
        "goal": goal,
        "user_intent": intent or {},
        "case": cases[0],
        "plan_context": {
            "input_mode": plan.get("input_mode"),
            "goal_context": plan.get("goal_context") or {},
        },
    }
    return (
        "You are correcting one rejected benchmark contract. Return exactly one "
        'JSON object shaped as {"contracts": [{...}]}; return no markdown or '
        "commentary. Regenerate the complete contract from the supplied case and "
        "schema. Preserve case_id and case meaning. Correct the reported structural "
        "or validation error without inventing facts, evidence, tool calls, or "
        "session history. When no reliable deterministic condition is supported by "
        "the input case, return automated_checks as an empty list; never invent "
        "placeholder checks. All criterion weights must be positive and sum to exactly "
        "100.\n"
        f"CORRECTION_ATTEMPT: {correction_number}/{max_corrections}\n"
        f"VALIDATION_ERROR: {validation_error[:2000]}\n"
        "Allowed check types: "
        + ", ".join(sorted(_ALLOWED_CHECKS))
        + "\nCONTRACT_SCHEMA:\n"
        + json.dumps(_prompt_contract_schema(), ensure_ascii=False, indent=2)
        + "\nINPUT:\n"
        + json.dumps(payload, ensure_ascii=False, default=str)
    )


def _prompt_contract_schema() -> dict[str, Any]:
    """Canonical example shared by the prompt and validator field constants."""
    task_contract = {
        field: "case-specific user intent" if field == "user_intent" else ["case-specific item"]
        for field in _REQUIRED_TASK_FIELDS
    }
    criterion = {
        "id": "criterion_id",
        "name": "Criterion name",
        "weight": 100,
        "description": "What this criterion measures",
        "score_1": "Fully satisfies the criterion",
        "score_075": "Mostly satisfies it with minor gaps",
        "score_05": "Partially satisfies it",
        "score_025": "Makes limited useful progress",
        "score_0": "Does not satisfy it",
    }
    return {
        "contracts": [
            {
                "schema_version": SCHEMA_VERSION,
                "case_id": "copy exactly from input",
                "template_id": "copy template_id when present, otherwise case_id",
                "case_type": "copy from input",
                "split": "copy from input",
                "source_session_id": "copy real session_id for historical cases; empty for direct_goal",
                "task_contract": task_contract,
                "grading_strategy": {
                    "grading_type": "llm_judge",
                    "criteria": [criterion],
                },
                "automated_checks": [],
                "replayability": {
                    "query_available": True,
                    "required_context": [],
                    "missing_context": [],
                    "replayable": True,
                },
                "provenance": {
                    "source_fields": ["case.query", "case.expected_behavior"],
                    "confidence": 0.8,
                    "unsupported_claims": [],
                },
            }
        ]
    }


def _extract_batch_contracts(
    payload: Any, *, expected_count: int, batch_number: int
) -> list[Any]:
    """Normalize supported transport envelopes into a contract list."""
    if isinstance(payload, list):
        contracts = payload
    elif isinstance(payload, dict) and "contracts" in payload:
        contracts = payload.get("contracts")
    elif isinstance(payload, dict) and expected_count == 1:
        # Be tolerant of a single contract object for a one-case batch. The
        # prompt still requests the canonical {"contracts": [...]} envelope.
        contracts = [payload]
    else:
        raise ValueError(
            f"case contract agent returned no contracts for batch {batch_number}; "
            "expected a JSON array or an object containing a contracts array"
        )
    if not isinstance(contracts, list):
        raise ValueError(
            f"case contract agent returned invalid contracts for batch {batch_number}; "
            "contracts must be an array"
        )
    if len(contracts) != expected_count:
        raise ValueError(
            f"case contract count mismatch for batch {batch_number}: "
            f"expected {expected_count}, got {len(contracts)}"
        )
    return contracts


def _order_batch_contracts(
    contracts: list[Any], *, batch: list[dict[str, Any]], batch_number: int
) -> list[dict[str, Any]]:
    """Validate case identity and restore the deterministic input case order."""
    expected_ids = [_case_id(case) for case in batch]
    if any(not case_id for case_id in expected_ids):
        raise ValueError(f"input case_id missing for contract batch {batch_number}")
    if len(set(expected_ids)) != len(expected_ids):
        raise ValueError(f"duplicate input case_id in contract batch {batch_number}")

    contracts_by_id: dict[str, dict[str, Any]] = {}
    for index, contract in enumerate(contracts, start=1):
        if not isinstance(contract, dict):
            raise ValueError(
                f"contract {index} for batch {batch_number} must be a JSON object"
            )
        case_id = str(contract.get("case_id") or "").strip()
        if not case_id:
            raise ValueError(
                f"contract {index} for batch {batch_number} is missing case_id"
            )
        if case_id in contracts_by_id:
            raise ValueError(
                f"duplicate contract case_id {case_id!r} in batch {batch_number}"
            )
        contracts_by_id[case_id] = contract

    expected_set = set(expected_ids)
    actual_set = set(contracts_by_id)
    missing = sorted(expected_set - actual_set)
    unexpected = sorted(actual_set - expected_set)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if unexpected:
            details.append(f"unexpected={unexpected}")
        raise ValueError(
            f"contract case_id mismatch for batch {batch_number}: " + ", ".join(details)
        )
    return [contracts_by_id[case_id] for case_id in expected_ids]


def _extract_json(text: str) -> Any:
    """Parse one JSON value or a whitespace-delimited JSON value stream.

    The canonical agent response is one JSON object, but accepting a complete
    NDJSON stream prevents a harmless model formatting variation from failing
    the Plan run. Non-whitespace garbage between or after values is rejected.
    """
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    if not raw:
        raise ValueError("agent response does not contain JSON")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = min((position for position in (raw.find("{"), raw.find("[")) if position >= 0), default=-1)
    if start < 0:
        raise ValueError("agent response does not contain JSON")

    decoder = json.JSONDecoder()
    position = start
    values: list[Any] = []
    while position < len(raw):
        while position < len(raw) and raw[position].isspace():
            position += 1
        if position >= len(raw):
            break
        try:
            value, end = decoder.raw_decode(raw, position)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"agent response contains invalid JSON near character {position}"
            ) from exc
        values.append(value)
        position = end

    if not values:
        raise ValueError("agent response does not contain JSON")
    return values[0] if len(values) == 1 else values


def _write_artifact(output_dir: Path, name: str, value: dict[str, Any]) -> None:
    path = output_dir / f"{name}.json"
    atomic_write_json(path, value)


def _write_contract_audit(
    output_dir: Path,
    contracts: list[dict[str, Any]],
    batches: list[dict[str, Any]],
) -> None:
    if any(batch.get("status") == "failed" for batch in batches):
        status = "failed"
    elif any(batch.get("status") == "fallback" for batch in batches):
        status = "fallback"
    else:
        status = "validated"
    _write_artifact(
        output_dir,
        "case_contract_audit",
        {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "contract_count": len(contracts),
            "batches": batches,
        },
    )


def _case_id(case: dict[str, Any]) -> str:
    return str(case.get("case_id") or case.get("session_id") or "").strip()


def _template_id(case: dict[str, Any]) -> str:
    return str(case.get("template_id") or case.get("case_id") or "").strip()


def _expected_source_session_id(case: dict[str, Any]) -> str:
    if str(case.get("case_type") or "").strip().lower() == "prospective":
        return ""
    return str(
        case.get("source_session_id") or case.get("session_id") or ""
    ).strip()
