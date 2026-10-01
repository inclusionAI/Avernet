"""
值规范化与结果合并 — LLM 返回值的清洗、校验与合并逻辑

所有 `safe_*` 函数负责将 LLM 返回的任意值转换为合法的内部类型。
所有 `normalize_*` 函数负责将 LLM 原始 JSON 规范化为标准结构。
"""

from __future__ import annotations

import re
from typing import Any, Literal


# ── 常量 ──────────────────────────────────────────────────

BUILTIN_TOOLS: set[str] = {
    "exec", "read", "write", "search", "process",
    "message", "cron", "list", "replace", "edit",
}

# 类型别名
PreliminaryStatus = Literal["success_clean", "success_suspected_retry", "failure", "unclear"]
ExecStatus = Literal["success", "failure"]
IsCompleteValue = Literal[0, 1, "unknown"]


# ── 值安全转换 ────────────────────────────────────────────

def safe_int_or_unknown(val: Any) -> int | Literal["unknown"]:
    """安全地将值转为 1/0/'unknown'

    Args:
        val: 任意输入值

    Returns:
        - 1 如果 val 为真值（1, "1", True）
        - 0 如果 val 为假值（0, "0", False）
        - "unknown" 其他情况
    """
    if val in (1, "1", True):
        return 1
    if val in (0, "0", False):
        return 0
    return "unknown"


def safe_bool_or_null(val: Any) -> bool | None:
    """安全地将值转为 bool 或 null

    Args:
        val: 任意输入值

    Returns:
        - True 如果 val 为真（True, 1, "true"）
        - False 如果 val 为假（False, 0, "false"）
        - None 其他情况
    """
    if val is True or val == 1 or val == "true":
        return True
    if val is False or val == 0 or val == "false":
        return False
    return None


def safe_positive_int_or_null(val: Any) -> int | None:
    """安全地将值转为正整数或 null

    Args:
        val: 任意输入值

    Returns:
        - 正整数（> 0）如果转换成功
        - None 如果转换失败或值非正
    """
    try:
        n = int(val)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None



def safe_human_intervention_level(val: Any) -> str:
    """规范化人工干预程度标签。"""
    s = str(val or "").strip().lower()
    return s if s in {"none", "light", "deep", "cron", "unknown"} else "unknown"


def safe_non_negative_int(val: Any) -> int:
    """安全地将值转为非负整数，失败返回 0。"""
    try:
        n = int(val)
        return n if n >= 0 else 0
    except (TypeError, ValueError):
        return 0


def safe_int_list(val: Any) -> list[int]:
    """安全地将值转为整数列表。"""
    if not isinstance(val, list):
        return []
    out: list[int] = []
    for item in val:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out

def safe_upper_snake_or_unknown(val: Any) -> str | None:
    """安全地将值转为合法的 UPPER_SNAKE_CASE 分类码，或 null

    Args:
        val: 任意输入值

    Returns:
        - UPPER_SNAKE_CASE 字符串（最多5个单词）
        - "UNKNOWN" 如果值格式不正确但不为空
        - None 如果值为空或 null
    """
    if val is None or val == "" or str(val).lower() in ("null", "none", "unknown"):
        return None
    s = str(val).strip()
    # UPPER_SNAKE_CASE: 以大写字母开头，可包含大写字母、数字、下划线
    if re.fullmatch(r"[A-Z][A-Z0-9]*(_[A-Z0-9]+){0,4}", s):
        return s
    return "UNKNOWN"


# 任务级失败分类白名单 (与 prompt 14 类 + COMPLETED/UNKNOWN 一致)
_TASK_FAILURE_CLASS_WHITELIST = frozenset({
    # 能力类
    "CAPABILITY_BOUNDARY", "TOOL_FAILURE", "WORKFLOW_FAILURE",
    "CONFIG_MISSING", "PERMISSION_NETWORK", "DATA_ISSUE",
    "PARAMETER_ERROR", "OUTPUT_WRONG",
    # 非能力类
    "AWAITING_USER", "EXEC_INTERRUPTED", "ASYNC_PENDING",
    "TRUNCATED", "NO_REPLY_IDLE", "NO_TASK",
    # 兜底与正常
    "UNKNOWN", "COMPLETED",
})


def _normalize_task_failure_class(val: Any) -> str:
    """规范化 task_failure_class: 归一为白名单 14 类码之一 / COMPLETED / UNKNOWN。

    空值归 "UNKNOWN" (而非 None, 便于下游聚合时该列非空、可 GROUP BY)。
    非法值(不在白名单)归 "UNKNOWN" (避免 LLM 编造分类码污染统计)。
    大小写不敏感(输入小写也接受), 输出统一大写。
    """
    if val is None or val == "":
        return "UNKNOWN"
    s = str(val).strip().upper()
    if not s:
        return "UNKNOWN"
    return s if s in _TASK_FAILURE_CLASS_WHITELIST else "UNKNOWN"


# ── 工具名合法性 ──────────────────────────────────────────

def is_valid_tool_name(name: str, expected_set: set[str]) -> bool:
    """判断工具名称是否合法

    Args:
        name: 工具名称
        expected_set: 预期工具名集合

    Returns:
        True 如果工具名为 MCP 工具（以 "mcp." 开头）或在预期集合中，
        且不是内置工具
    """
    if name in BUILTIN_TOOLS:
        return False
    if name.startswith("mcp."):
        return True
    return name in expected_set


# ── LLM 返回值规范化 ─────────────────────────────────────

def normalize_is_complete(raw: dict[str, Any]) -> dict[str, Any]:
    """规范化 is_complete 独立调用的 LLM 返回

    Args:
        raw: LLM 返回的原始 JSON

    Returns:
        规范化后的结果，包含完成度字段，并兼容非周期任务人工干预字段。
    """
    reasoning = str(raw.get("reasoning", "") or "")
    human_reasoning = str(raw.get("human_intervention_reasoning", "") or "").strip()
    if human_reasoning:
        human_reasoning = human_reasoning[:50]

    return {
        "is_complete": safe_int_or_unknown(raw.get("is_complete")),
        "reasoning": reasoning,
        "task_description": str(raw.get("task_description", "") or "").strip()[:100] if raw.get("task_description") else "",
        # 任务级失败分类: LLM 在 is_complete=0 时必填 14 类之一; 1 填 COMPLETED; unknown 填 UNKNOWN
        "task_failure_class": _normalize_task_failure_class(raw.get("task_failure_class")),
        "human_intervention_level": safe_human_intervention_level(raw.get("human_intervention_level")),
        "human_intervention_reasoning": human_reasoning,
        "human_intervention_evidence_message_indices": safe_int_list(raw.get("human_intervention_evidence_message_indices")),
        "human_turn_count": safe_non_negative_int(raw.get("human_turn_count")),
        "system_user_message_count": safe_non_negative_int(raw.get("system_user_message_count")),
    }


def normalize_preliminary_status(val: Any) -> PreliminaryStatus:
    """规范化 preliminary_status 字段

    Args:
        val: LLM 返回的 preliminary_status 值

    Returns:
        规范化后的状态字符串
    """
    s = str(val).strip().lower() if val else ""
    mapping: dict[str, PreliminaryStatus] = {
        "success_clean": "success_clean",
        "success clean": "success_clean",
        "clean": "success_clean",
        "success_suspected_retry": "success_suspected_retry",
        "suspected_retry": "success_suspected_retry",
        "retry": "success_suspected_retry",
        "success_retry": "success_suspected_retry",
        "failure": "failure",
        "fail": "failure",
        "failed": "failure",
    }
    return mapping.get(s, "unclear")


def _compute_preliminary_status(status: str, retry_detected: bool | None) -> str:
    """从 execution 字段推导 preliminary_status（向后兼容）

    统一评估后 LLM 不再输出 preliminary_status，由 execution 推导：
    - success + retry_detected=false → success_clean
    - success + retry_detected=true  → success_suspected_retry
    - success + retry_detected=null  → success_suspected_retry (保守：不确定时假设有重试)
    - failure                        → failure
    - 其他                           → unclear

    Args:
        status: "success" 或 "failure"
        retry_detected: True/False/None

    Returns:
        preliminary_status 字符串
    """
    if status == "success":
        if retry_detected is False:
            return "success_clean"
        return "success_suspected_retry"
    if status == "failure":
        return "failure"
    return "unclear"


def normalize_tool_inspector(
    raw: dict[str, Any],
    key: Literal["skills", "mcps"],
    expected_names: list[str],
    extra_valid_names: set[str] | None = None
) -> list[dict[str, Any]]:
    """规范化 skill/mcp inspector 的 LLM 返回

    Args:
        raw: LLM 返回的 JSON
        key: "skills" 或 "mcps"
        expected_names: 预期应出现的工具名列表
        extra_valid_names: 额外合法名称集合（如 mcp 前缀匹配用）

    Returns:
        规范化后的工具评估列表，每个元素包含:
        - name: 工具名
        - is_correct: 1/0/"unknown"
        - preliminary_status: 状态字符串
    """
    if extra_valid_names is None:
        extra_valid_names = set()

    expected = set(expected_names) | extra_valid_names
    result: list[dict[str, Any]] = []

    raw_items = raw.get(key, [])
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or not is_valid_tool_name(name, expected):
                continue
            result.append({
                "name": name,
                "is_correct": safe_int_or_unknown(item.get("is_correct")),
                "preliminary_status": normalize_preliminary_status(item.get("preliminary_status")),
            })

    # 补齐 LLM 未返回的预期工具名
    for tool_name in expected_names:
        if not tool_name:
            continue
        if key == "skills" and tool_name in BUILTIN_TOOLS:
            continue
        if not any(t["name"] == tool_name for t in result):
            result.append({
                "name": tool_name,
                "is_correct": "unknown",
                "preliminary_status": "unclear",
            })

    return result


def normalize_tool_dive(key: Literal["skills", "mcps"], raw: dict[str, Any]) -> list[dict[str, Any]]:
    """规范化 Phase 2 (ToolDive) 的 LLM 返回

    .. deprecated::
        Phase 1+2 已合并为统一评估。请使用 normalize_tool_assessment 代替。
        此函数保留以兼容旧数据或外部调用。

    Args:
        key: "skills" 或 "mcps"
        raw: LLM 返回的 JSON

    Returns:
        规范化后的工具深度评估列表，每个元素包含:
        - name: 工具名
        - is_correct: 1/0/"unknown"
        - execution: 执行详情对象
    """
    result: list[dict[str, Any]] = []
    raw_items = raw.get(key, [])
    if not isinstance(raw_items, list):
        return result

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue

        execution = item.get("execution", {})
        if not isinstance(execution, dict):
            execution = {}

        retry_detected = safe_bool_or_null(execution.get("retry_detected"))
        retry_count = safe_positive_int_or_null(execution.get("retry_count"))

        if retry_detected is None:
            retry_count = None

        failure_category = safe_upper_snake_or_unknown(execution.get("failure_category"))

        success_experience = normalize_success_experience(execution.get("success_experience"))

        result.append({
            "name": name,
            "is_correct": safe_int_or_unknown(item.get("is_correct")),
            "execution": {
                "status": "success" if str(execution.get("status", "")).lower() == "success" else "failure",
                "retry_detected": retry_detected,
                "retry_count": retry_count,
                "success_experience": success_experience,
                "failure_category": failure_category,
            }
        })

    return result


def normalize_tool_assessment(
    raw: dict[str, Any],
    key: Literal["skills", "mcps"],
    expected_names: list[str],
    extra_valid_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """规范化统一评估（原 Phase 1+2 合并）的 LLM 返回

    统一评估输出包含 is_correct + execution（含 status/retry_detected/retry_count/
    success_experience/failure_category），同时推导 preliminary_status 保持向后兼容。

    Args:
        raw: LLM 返回的 JSON
        key: "skills" 或 "mcps"
        expected_names: 预期应出现的工具名列表
        extra_valid_names: 额外合法名称集合（如 mcp 前缀匹配用）

    Returns:
        规范化后的工具评估列表，每个元素包含:
        - name: 工具名
        - is_correct: 1/0/"unknown"
        - preliminary_status: 从 execution 推导的状态字符串（向后兼容）
        - execution: 执行详情对象（含 status, retry_detected, retry_count,
          success_experience, failure_category）
    """
    if extra_valid_names is None:
        extra_valid_names = set()

    expected = set(expected_names) | extra_valid_names
    result: list[dict[str, Any]] = []

    raw_items = raw.get(key, [])
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or not is_valid_tool_name(name, expected):
                continue

            execution = item.get("execution", {})
            if not isinstance(execution, dict):
                execution = {}

            retry_detected = safe_bool_or_null(execution.get("retry_detected"))
            retry_count = safe_positive_int_or_null(execution.get("retry_count"))

            # retry_detected=null 时 retry_count 必须为 null
            if retry_detected is None:
                retry_count = None
            # retry_detected=false 时 retry_count 必须为 0
            elif retry_detected is False:
                retry_count = 0

            failure_category = safe_upper_snake_or_unknown(execution.get("failure_category"))
            success_experience = normalize_success_experience(execution.get("success_experience"))

            status = "success" if str(execution.get("status", "")).lower() == "success" else "failure"
            preliminary_status = _compute_preliminary_status(status, retry_detected)

            result.append({
                "name": name,
                "is_correct": safe_int_or_unknown(item.get("is_correct")),
                "preliminary_status": preliminary_status,
                "execution": {
                    "status": status,
                    "retry_detected": retry_detected,
                    "retry_count": retry_count,
                    "success_experience": success_experience,
                    "failure_category": failure_category,
                },
            })

    # 补齐 LLM 未返回的预期工具名
    for tool_name in expected_names:
        if not tool_name:
            continue
        if key == "skills" and tool_name in BUILTIN_TOOLS:
            continue
        if not any(t["name"] == tool_name for t in result):
            result.append({
                "name": tool_name,
                "is_correct": "unknown",
                "preliminary_status": "unclear",
                "execution": None,
            })

    return result


def normalize_success_experience(val: Any) -> dict[str, str] | None:
    """规范化 success_experience QA 对结构

    支持两种输入格式：
    1. 对象格式: {"problem": "xxx", "solution": "xxx"}
    2. 字符串格式: "xxx" -> 转换为 {"problem": "", "solution": "xxx"}（向后兼容）

    Args:
        val: LLM 返回的 success_experience 值

    Returns:
        规范化后的 QA 对，或 None
    """
    if val is None:
        return None

    # 对象格式
    if isinstance(val, dict):
        problem = val.get("problem")
        solution = val.get("solution")
        if problem or solution:
            return {
                "problem": str(problem).strip() if problem else "",
                "solution": str(solution).strip() if solution else "",
            }
        return None

    # 字符串格式（向后兼容）
    if isinstance(val, str):
        s = val.strip()
        if s:
            return {
                "problem": "",
                "solution": s,
            }
        return None

    return None


def merge_tool_assessment(
    phase1_list: list[dict[str, Any]],
    phase2_list: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """合并 Phase 1 和 Phase 2 的工具评估结果

    .. deprecated::
        Phase 1+2 已合并为统一评估（normalize_tool_assessment）。
        此函数保留以兼容旧数据或外部调用。

    Args:
        phase1_list: Phase 1 inspector 的结果列表
        phase2_list: Phase 2 dive 的结果列表

    Returns:
        合并后的结果列表，Phase 2 的 execution 覆盖 Phase 1 的 preliminary_status
    """
    phase2_map = {item["name"]: item for item in phase2_list}
    merged: list[dict[str, Any]] = []

    for p1 in phase1_list:
        name = p1["name"]
        p2 = phase2_map.get(name)

        if p2:
            merged.append({
                "name": name,
                "is_correct": p2.get("is_correct", p1.get("is_correct", "unknown")),
                "preliminary_status": p1.get("preliminary_status", "unclear"),
                "execution": p2.get("execution"),
            })
        else:
            merged.append({
                "name": name,
                "is_correct": p1.get("is_correct", "unknown"),
                "preliminary_status": p1.get("preliminary_status", "unclear"),
                "execution": None,
            })

    return merged