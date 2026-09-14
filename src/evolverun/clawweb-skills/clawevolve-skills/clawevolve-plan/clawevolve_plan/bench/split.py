from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from .. import logger


DEFAULT_TRAIN_RATIO = 0.8


def assign_train_test_splits(cases: list[dict[str, Any]], train_ratio: float = DEFAULT_TRAIN_RATIO) -> list[dict[str, Any]]:
    """Assign deterministic, leakage-safe train/test split for bench cases.

    This module only decides split metadata. It does not change template
    rendering, LLM judge, ClawWeb upload, or any broader plan workflow.

    Hard constraints:
    - Never duplicate cases.
    - Keep cases from the same source session/file in one split.
    - Keep at least one train case whenever any case exists.

    Soft goals:
    - Put high-value controllable bad cases in train.
    - Keep representative bad cases in test when sample size allows.
    - Add good regression cases to test when test has room.
    - Keep output deterministic through stable hashes.
    """
    items = [_normalize_case(case, index) for index, case in enumerate(cases)]
    total = len(items)
    groups = _build_groups(items)
    warnings = _initial_warnings(items, groups)
    target_test_count = _target_test_count(total, len(groups), train_ratio)

    logger.info(
        "bench split start",
        total=total,
        group_count=len(groups),
        train_ratio=train_ratio,
        target_test_count=target_test_count,
        strategy="session_grouped_stratified_split_v2",
        warnings=warnings,
    )

    if total == 0:
        logger.info("bench split done", total=0, train_count=0, test_count=0, warnings=warnings)
        return []

    if target_test_count <= 0:
        reason = "single_case_or_single_source_group_no_test"
        out = [_assign_split(item, "train", reason) for item in items]
        _log_split_summary(out, warnings=warnings + [reason])
        return out

    test_groups = _select_test_groups(groups, target_test_count, total)
    test_keys = {str(g["group_key"]) for g in test_groups}

    out: list[dict[str, Any]] = []
    for item in items:
        if item["group_key"] in test_keys:
            out.append(_assign_split(item, "test", _test_reason(item, test_groups)))
        else:
            out.append(_assign_split(item, "train", "remaining_group_kept_for_training"))

    out = _repair_group_level_edges(out, items, groups, target_test_count)
    _log_split_summary(out, warnings=warnings)
    return out


def build_split_audit(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a local audit artifact from already split cases."""
    train = [c for c in cases if _split_value(c) == "train"]
    test = [c for c in cases if _split_value(c) == "test"]
    warnings: list[str] = []
    case_warnings: list[dict[str, str]] = []
    if len(cases) == 1:
        warnings.append("case_count_too_small_for_test")
    if cases and not test:
        warnings.append("test_empty")
    prospective_only = bool(cases) and all(
        _case_type(case) == "prospective" for case in cases
    )
    if not prospective_only and not any(_case_type(c) == "bad" for c in cases):
        warnings.append("no_bad_case_for_failure_optimization")
    session_splits: dict[str, set[str]] = {}
    for c in cases:
        key = _source_group_key(c)
        session_splits.setdefault(key, set()).add(_split_value(c))
        if not _case_query(c):
            case_warnings.append({"caseId": str(c.get("case_id") or c.get("id") or ""), "reason": "missing_eval_query_or_query"})
    leaked = {k: sorted(v) for k, v in session_splits.items() if len(v) > 1 and k.startswith(("session:", "file:"))}
    if leaked:
        warnings.append("source_group_leakage_detected")
    return {
        "schema_version": "clawevolve-bench-split.v1",
        "strategy": "session_grouped_stratified_split_v2",
        "summary": {
            "total": len(cases),
            "usable": len(cases),
            "excluded": 0,
            "trainCount": len(train),
            "testCount": len(test),
            "badTrainCount": sum(1 for c in train if _case_type(c) == "bad"),
            "badTestCount": sum(1 for c in test if _case_type(c) == "bad"),
            "goodTrainCount": sum(1 for c in train if _case_type(c) == "good"),
            "goodTestCount": sum(1 for c in test if _case_type(c) == "good"),
            "prospectiveTrainCount": sum(
                1 for c in train if _case_type(c) == "prospective"
            ),
            "prospectiveTestCount": sum(
                1 for c in test if _case_type(c) == "prospective"
            ),
            "sourceGroupLeakageCount": len(leaked),
        },
        "warnings": sorted(set(warnings)),
        "items": [_audit_item(c) for c in cases],
        "caseWarnings": case_warnings,
        "excludedCases": [],
        "sourceGroupLeakage": leaked,
    }


def _normalize_case(case: dict[str, Any], index: int) -> dict[str, Any]:
    copied = dict(case)
    case_id = str(copied.get("case_id") or copied.get("id") or f"case_{index + 1:03d}").strip()
    source_session_id = _source_session_id(copied)
    case_type = _case_type(copied)
    failure_mode = _token(_case_value(copied, "evolution_failure_mode", "failure_mode", "symptom_class", "root_cause_class"), "unknown_failure_mode")
    cluster = _token(_case_value(copied, "root_cause_cluster_id", "root_cause_cluster", "cluster_id", "common_problem_key", "root_cause_class"), "unknown_cluster")
    quality = _quality_score(_case_value(copied, "quality_score"))
    optimization_value = _rank(_case_value(copied, "optimization_value"), {"high": 3.0, "medium": 2.0, "low": 1.0, "regression_guard": 0.6}, 1.5)
    controllability = _rank(_case_value(copied, "failure_controllability", "controllability"), {"high": 3.0, "medium": 2.0, "low": -1.0}, 0.5)
    has_query = bool(_case_query(copied))
    evidence_count = _evidence_count(copied)
    stable_seed = "|".join([case_id, source_session_id, case_type, failure_mode, cluster, str(index)])
    stable_hash = hashlib.sha1(stable_seed.encode("utf-8", errors="ignore")).hexdigest()
    group_key = _source_group_key(copied)
    train_score = _train_score(case_type, optimization_value, controllability, quality, evidence_count, has_query)
    test_score = _test_score(case_type, optimization_value, controllability, quality, evidence_count, has_query)
    item = {
        "case": copied,
        "index": index,
        "case_id": case_id,
        "source_session_id": source_session_id,
        "case_type": case_type,
        "failure_mode": failure_mode,
        "cluster": cluster,
        "quality_score": quality,
        "optimization_value_score": optimization_value,
        "controllability_score": controllability,
        "evidence_count": evidence_count,
        "has_query": has_query,
        "train_score": train_score,
        "test_score": test_score,
        "stable_hash": stable_hash,
        "group_key": group_key,
        "stratum_key": f"{case_type}|{failure_mode}|{cluster}",
    }
    logger.info(
        "bench split case normalized",
        index=index + 1,
        case_id=case_id,
        source_session_id=source_session_id,
        case_type=case_type,
        failure_mode=failure_mode,
        cluster=cluster,
        quality_score=quality,
        optimization_value_score=optimization_value,
        controllability_score=controllability,
        train_score=train_score,
        test_score=test_score,
        group_key=group_key,
        stratum_key=item["stratum_key"],
        has_query=has_query,
        evidence_count=evidence_count,
    )
    return item


def _build_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_key.setdefault(str(item["group_key"]), []).append(item)
    groups: list[dict[str, Any]] = []
    for group_key, group_items in by_key.items():
        case_types = {str(i["case_type"]) for i in group_items}
        failure_modes = {str(i["failure_mode"]) for i in group_items}
        clusters = {str(i["cluster"]) for i in group_items}
        group = {
            "group_key": group_key,
            "items": group_items,
            "size": len(group_items),
            "case_types": case_types,
            "failure_modes": failure_modes,
            "clusters": clusters,
            "has_bad": "bad" in case_types,
            "has_good": "good" in case_types,
            "train_score": max(float(i["train_score"]) for i in group_items),
            "test_score": max(float(i["test_score"]) for i in group_items),
            "stable_hash": min(str(i["stable_hash"]) for i in group_items),
        }
        groups.append(group)
    logger.info("bench split groups built", group_count=len(groups), preview=[_group_preview(g) for g in sorted(groups, key=lambda g: str(g["stable_hash"]))[:8]])
    return groups


def _target_test_count(total: int, group_count: int, train_ratio: float) -> int:
    if total <= 1 or group_count <= 1:
        return 0
    if total == 2:
        return 1
    if total <= 5:
        return 1
    if total <= 9:
        return min(total - 1, 2)
    raw = int(round(total * max(0.0, min(1.0, 1.0 - train_ratio))))
    return max(2, min(total - 1, raw))


def _select_test_groups(groups: list[dict[str, Any]], target_test_count: int, total: int) -> list[dict[str, Any]]:
    if not groups or target_test_count <= 0:
        return []
    if len(groups) == 1:
        logger.info("bench split test selection skipped", reason="single_source_group")
        return []
    if total <= 2:
        selected = _small_sample_test_groups(groups)
        logger.info("bench split small sample test selected", selected=[_group_preview(g) for g in selected])
        return selected

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    covered_modes: set[str] = set()
    covered_clusters: set[str] = set()

    for group in sorted(groups, key=lambda g: _test_group_key(g, covered_modes, covered_clusters)):
        if _count(selected) >= target_test_count:
            break
        if group["group_key"] in selected_keys:
            continue
        if _would_leave_train_empty(selected + [group], total):
            continue
        if _overshoots(selected, group, target_test_count):
            continue
        diversity_gain = bool(group["failure_modes"] - covered_modes or group["clusters"] - covered_clusters)
        if selected and not diversity_gain and _remaining_diverse(groups, selected_keys, covered_modes, covered_clusters):
            continue
        selected.append(group)
        selected_keys.add(group["group_key"])
        covered_modes.update(group["failure_modes"])
        covered_clusters.update(group["clusters"])
        logger.info(
            "bench split test group selected",
            group_key=group["group_key"],
            group_size=group["size"],
            reason="representative_bad_or_diverse_group",
            selected_test_count=_count(selected),
            target_test_count=target_test_count,
            failure_modes=sorted(group["failure_modes"]),
            clusters=sorted(group["clusters"]),
            train_score=group["train_score"],
            test_score=group["test_score"],
        )

    while _count(selected) < target_test_count:
        remaining = [g for g in groups if g["group_key"] not in selected_keys and not _would_leave_train_empty(selected + [g], total)]
        if not remaining:
            logger.info("bench split test fill stopped", reason="no_remaining_group_without_empty_train", selected_test_count=_count(selected), target_test_count=target_test_count)
            break
        group = sorted(remaining, key=lambda g: _test_group_key(g, covered_modes, covered_clusters))[0]
        selected.append(group)
        selected_keys.add(group["group_key"])
        covered_modes.update(group["failure_modes"])
        covered_clusters.update(group["clusters"])
        logger.info("bench split test group filled", group_key=group["group_key"], group_size=group["size"], reason="fill_target_test_count", selected_test_count=_count(selected), target_test_count=target_test_count)

    selected = _maybe_add_good_regression(groups, selected, target_test_count, total)
    return selected


def _small_sample_test_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # For 2 cases from different source groups: keep bad/high-value optimization
    # anchor in train when possible; put good or lower training-score group in test.
    if len(groups) <= 1:
        return []
    if len(groups) == 2:
        bad_groups = [g for g in groups if g.get("has_bad")]
        good_groups = [g for g in groups if g.get("has_good") and not g.get("has_bad")]
        if len(bad_groups) == 1 and good_groups:
            return [sorted(good_groups, key=lambda g: str(g["stable_hash"]))[0]]
        return [sorted(groups, key=lambda g: (float(g.get("train_score") or 0), str(g.get("stable_hash") or "")))[0]]
    return [sorted(groups, key=lambda g: _test_group_key(g, set(), set()))[0]]


def _maybe_add_good_regression(groups: list[dict[str, Any]], selected: list[dict[str, Any]], target_test_count: int, total: int) -> list[dict[str, Any]]:
    if target_test_count < 2 or any(g.get("has_good") for g in selected):
        return selected
    selected_keys = {g["group_key"] for g in selected}
    candidates = [g for g in groups if g.get("has_good") and g["group_key"] not in selected_keys]
    if not candidates:
        return selected
    candidate = sorted(candidates, key=lambda g: (int(g["size"]), str(g["stable_hash"]))) [0]
    if _count(selected) + int(candidate["size"]) <= target_test_count and not _would_leave_train_empty(selected + [candidate], total):
        logger.info("bench split good regression group added", group_key=candidate["group_key"], group_size=candidate["size"], reason="test_has_room_for_good_regression")
        return selected + [candidate]
    return selected


def _repair_group_level_edges(out: list[dict[str, Any]], items: list[dict[str, Any]], groups: list[dict[str, Any]], target_test_count: int) -> list[dict[str, Any]]:
    # Do not move individual cases here; that can break source-session leakage protection.
    train = [c for c in out if _split_value(c) == "train"]
    test = [c for c in out if _split_value(c) == "test"]
    if not train and test:
        largest_test_group = sorted(_groups_from_split(out, "test"), key=lambda g: (-len(g[1]), g[0]))[0]
        logger.info("bench split group repair", action="move_group_to_train", group_key=largest_test_group[0], reason="train_must_not_be_empty")
        return [_set_split(c, "train", "group_repair_create_non_empty_train") if c.get("split_group_key") == largest_test_group[0] else c for c in out]
    if train and not test and target_test_count > 0 and len(groups) > 1:
        train_groups = _groups_from_split(out, "train")
        candidates = [g for g in train_groups if len(train) - len(g[1]) > 0]
        if candidates:
            group_key, _cases = sorted(candidates, key=lambda g: (len(g[1]), g[0]))[0]
            logger.info("bench split group repair", action="move_group_to_test", group_key=group_key, reason="create_non_empty_test_without_case_level_leakage")
            return [_set_split(c, "test", "group_repair_create_non_empty_test") if c.get("split_group_key") == group_key else c for c in out]
    return out


def _groups_from_split(cases: list[dict[str, Any]], split: str) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        if _split_value(c) == split:
            grouped.setdefault(str(c.get("split_group_key") or _source_group_key(c)), []).append(c)
    return list(grouped.items())


def _set_split(case: dict[str, Any], split: str, reason: str) -> dict[str, Any]:
    out = dict(case)
    out["split"] = split
    out["case_split"] = split
    out["split_reason"] = reason
    return out


def _assign_split(item: dict[str, Any], split: str, reason: str) -> dict[str, Any]:
    out = dict(item["case"])
    out.setdefault("case_id", item["case_id"])
    out.setdefault("source_session_id", item["source_session_id"])
    out.setdefault("root_cause_cluster_id", item["cluster"])
    out["split"] = split
    out["case_split"] = split
    out["split_reason"] = reason
    out["split_group_key"] = item["group_key"]
    out["split_stratum_key"] = item["stratum_key"]
    out["split_scores"] = {
        "train": round(float(item["train_score"]), 3),
        "test": round(float(item["test_score"]), 3),
        "quality": round(float(item["quality_score"]), 3),
        "optimizationValue": round(float(item["optimization_value_score"]), 3),
        "controllability": round(float(item["controllability_score"]), 3),
    }
    return out


def _test_reason(item: dict[str, Any], selected_groups: list[dict[str, Any]]) -> str:
    group = next((g for g in selected_groups if g["group_key"] == item["group_key"]), None)
    if group and group.get("has_good") and not group.get("has_bad"):
        return "selected_good_regression_group_for_test"
    if item["case_type"] == "bad":
        return "selected_representative_bad_group_for_generalization"
    return "selected_representative_group_for_test"


def _test_group_key(group: dict[str, Any], covered_modes: set[str], covered_clusters: set[str]) -> tuple[Any, ...]:
    diversity = bool(group["failure_modes"] - covered_modes or group["clusters"] - covered_clusters)
    return (
        0 if group.get("has_bad") else 1,
        0 if diversity else 1,
        -float(group.get("test_score") or 0),
        int(group.get("size") or 0),
        str(group.get("stable_hash") or ""),
    )


def _train_score(case_type: str, opt: float, ctrl: float, quality: float, evidence_count: int, has_query: bool) -> float:
    base = 40.0 if case_type == "bad" else 10.0 if case_type == "good" else 0.0
    evidence = min(10.0, evidence_count * 2.0)
    query = 5.0 if has_query else -20.0
    return base + opt * 8.0 + ctrl * 6.0 + quality * 20.0 + evidence + query


def _test_score(case_type: str, opt: float, ctrl: float, quality: float, evidence_count: int, has_query: bool) -> float:
    base = 35.0 if case_type == "bad" else 15.0 if case_type == "good" else 0.0
    evidence = min(8.0, evidence_count * 1.5)
    query = 8.0 if has_query else -30.0
    return base + opt * 5.0 + ctrl * 4.0 + quality * 25.0 + evidence + query


def _overshoots(selected: list[dict[str, Any]], group: dict[str, Any], target: int) -> bool:
    current = _count(selected)
    size = int(group.get("size") or 0)
    if current + size <= target:
        return False
    # Allow slight overshoot for grouped sessions, but avoid swallowing most data.
    return (current + size - target) > max(1, target // 2)


def _would_leave_train_empty(selected: list[dict[str, Any]], total: int) -> bool:
    return _count(selected) >= total


def _remaining_diverse(groups: list[dict[str, Any]], selected_keys: set[str], covered_modes: set[str], covered_clusters: set[str]) -> bool:
    for group in groups:
        if group["group_key"] in selected_keys:
            continue
        if group["failure_modes"] - covered_modes or group["clusters"] - covered_clusters:
            return True
    return False


def _count(groups: list[dict[str, Any]]) -> int:
    return sum(int(g.get("size") or len(g.get("items") or [])) for g in groups)


def _initial_warnings(items: list[dict[str, Any]], groups: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if len(items) == 1:
        warnings.append("case_count_too_small_for_test")
    if len(items) > 1 and len(groups) == 1:
        warnings.append("all_cases_from_same_source_group")
    prospective_only = bool(items) and all(
        item["case_type"] == "prospective" for item in items
    )
    if (
        not prospective_only
        and items
        and not any(item["case_type"] == "bad" for item in items)
    ):
        warnings.append("no_bad_case_for_failure_optimization")
    if (
        not prospective_only
        and items
        and not any(item["case_type"] == "good" for item in items)
    ):
        warnings.append("no_good_regression_case")
    if any(not i["has_query"] for i in items):
        warnings.append("case_missing_eval_query_or_query")
    return warnings


def _log_split_summary(cases: list[dict[str, Any]], warnings: list[str]) -> None:
    train = [c for c in cases if _split_value(c) == "train"]
    test = [c for c in cases if _split_value(c) == "test"]
    by_split_type = Counter(f"{_split_value(c)}:{_case_type(c)}" for c in cases)
    session_splits: dict[str, set[str]] = {}
    for c in cases:
        key = _source_group_key(c)
        if key.startswith(("session:", "file:")):
            session_splits.setdefault(key, set()).add(_split_value(c))
    leaked = {key: sorted(value) for key, value in session_splits.items() if len(value) > 1}
    logger.info(
        "bench split done",
        total=len(cases),
        train_count=len(train),
        test_count=len(test),
        split_type_counts=dict(by_split_type),
        leakage_source_group_count=len(leaked),
        leakage_preview=leaked,
        warnings=warnings,
        items_preview=[_audit_item(c) for c in cases[:8]],
    )


def _audit_item(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "caseId": str(case.get("case_id") or case.get("id") or ""),
        "sourceSessionId": str(case.get("source_session_id") or case.get("session_id") or ""),
        "sourceGroupKey": str(case.get("split_group_key") or _source_group_key(case)),
        "caseType": _case_type(case),
        "failureMode": str(case.get("evolution_failure_mode") or case.get("failure_mode") or ""),
        "rootCauseCluster": str(case.get("root_cause_cluster_id") or case.get("root_cause_cluster") or case.get("cluster_id") or case.get("common_problem_key") or ""),
        "qualityScore": _quality_score(_case_value(case, "quality_score")),
        "split": _split_value(case),
        "reason": str(case.get("split_reason") or ""),
        "scores": case.get("split_scores") or {},
    }


def _group_preview(group: dict[str, Any]) -> dict[str, Any]:
    return {
        "group_key": group.get("group_key"),
        "size": group.get("size"),
        "case_types": sorted(group.get("case_types") or []),
        "failure_modes": sorted(group.get("failure_modes") or []),
        "clusters": sorted(group.get("clusters") or []),
        "train_score": round(float(group.get("train_score") or 0), 3),
        "test_score": round(float(group.get("test_score") or 0), 3),
    }


def _case_value(case: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = case.get(key)
        if value not in (None, "", []):
            return value
    judge = case.get("judge") if isinstance(case.get("judge"), dict) else {}
    for key in keys:
        value = judge.get(key)
        if value not in (None, "", []):
            return value
    return ""


def _source_session_id(case: dict[str, Any]) -> str:
    return str(case.get("source_session_id") or case.get("session_id") or case.get("conversation_id") or case.get("trace_id") or "").strip()


def _source_group_key(case: dict[str, Any]) -> str:
    session = _source_session_id(case)
    if session:
        return f"session:{session}"
    source_file = str(case.get("source_file") or case.get("session_path") or case.get("raw_session_copy_path") or "").strip()
    if source_file:
        return f"file:{source_file}"
    case_id = str(case.get("case_id") or case.get("id") or "unknown_case").strip()
    return f"case:{case_id}"


def _case_type(case: dict[str, Any]) -> str:
    raw = str(_case_value(case, "case_type", "label", "status") or "").strip().lower()
    if raw in {"good", "success", "succeeded", "pass", "passed", "good_regression", "regression"}:
        return "good"
    if raw in {"bad", "failed", "failure", "fail", "error"}:
        return "bad"
    if raw in {"prospective", "goal", "synthetic"}:
        return "prospective"
    return "unknown"


def _case_query(case: dict[str, Any]) -> str:
    return str(case.get("eval_query") or case.get("query") or case.get("prompt") or "").strip()


def _evidence_count(case: dict[str, Any]) -> int:
    count = 0
    for key in ("evidence", "evidence_file_hints", "tool_hints"):
        value = case.get(key)
        if isinstance(value, list):
            count += len(value)
        elif value:
            count += 1
    return count


def _quality_score(value: Any) -> float:
    raw = str(value or "").strip().lower()
    if raw in {"high", "good"}:
        return 0.9
    if raw in {"medium", "mid", "normal"}:
        return 0.6
    if raw in {"low", "bad"}:
        return 0.3
    try:
        num = float(raw)
    except ValueError:
        return 0.6
    if num > 1.0:
        num = num / 100.0 if num <= 100.0 else 1.0
    return max(0.0, min(1.0, num))


def _rank(value: Any, mapping: dict[str, float], default: float) -> float:
    raw = str(value or "").strip().lower()
    if raw in mapping:
        return mapping[raw]
    try:
        return float(raw)
    except ValueError:
        return default


def _token(value: Any, default: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    token = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")[:80]
    if token:
        return token
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{default}_{digest}"


def _split_value(case: dict[str, Any]) -> str:
    raw = str(case.get("split") or case.get("case_split") or "train").strip().lower()
    return "train" if raw in {"train", "training"} else "test"
