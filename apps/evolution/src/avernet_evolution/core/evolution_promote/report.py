"""候选报告（08 §2.5）：只做投影，不存储。

报告把三处事实拼在一起——实验记录（人话与证据）、验证判定（度量）、门禁决定与
评审状态（决定）。规范要求金额/差值是十进制字符串，因此这里统一格式化。
"""

from __future__ import annotations

from typing import Any

from ..evolution_verify.enums import OverfitFlag, Split
from .enums import ReviewStatus
from .risk import tier_of_op

__all__ = [
    "cost_summary",
    "op_items",
    "op_target",
    "patch_summary",
    "report_flags",
    "review_json",
    "split_summaries",
]


def _dec(value: float | None) -> str | None:
    return None if value is None else f"{value:.4f}"


def op_target(op: Any) -> str:
    """补丁操作的"改了什么"：不同 op 的目标字段名不同。"""

    for name in ("target", "type", "name", "path", "key"):
        value = getattr(op, name, None)
        if isinstance(value, str) and value:
            return value
    item = getattr(op, "item", None)
    key = getattr(item, "key", None)
    if isinstance(key, str):
        return f"memory/{key}"
    mode = getattr(op, "mode", None)
    if mode is not None:
        return f"memory mode={getattr(mode, 'value', mode)}"
    return ""


def patch_summary(patch: dict[str, Any] | None) -> list[dict[str, Any]]:
    """补丁摘要。旧记录的 `patch` 只有计数，这种情况下退化为一条汇总。"""

    if not patch:
        return []
    items = patch.get("items")
    if isinstance(items, list) and items:
        return [
            {
                "op_index": index,
                "op": str(item.get("op", "")),
                "target": str(item.get("target", "")),
                "tier": str(item.get("tier") or "T2"),
                "reason": str(item.get("reason", "")),
            }
            for index, item in enumerate(items)
        ]
    count = patch.get("ops")
    if isinstance(count, int):
        return [
            {
                "op_index": index,
                "op": "unknown",
                "target": "",
                "tier": "T2",
                "reason": "recorded before per-op summaries were stored",
            }
            for index in range(count)
        ]
    return []


def split_summaries(verdict: Any) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for split, result in (verdict.splits or {}).items():
        ci = result.ci or (None, None)
        summaries.append(
            {
                "split": split.value if isinstance(split, Split) else str(split),
                "mean_delta": _dec(result.mean_delta),
                "ci_low": _dec(ci[0]),
                "ci_high": _dec(ci[1]),
                "newly_failing": len(result.newly_failing),
                # 逐用例细节不对策略可见；评审者读的是评估记录本身。
                "detail_visible": True,
            }
        )
    return summaries


def report_flags(verdict: Any) -> list[str]:
    flags: list[str] = []
    if OverfitFlag.GAIN_RATIO_EXCEEDED in tuple(verdict.overfit_flags or ()):
        flags.append("overfit_suspect")
    if OverfitFlag.SEED_FLUCTUATION_EXCEEDED in tuple(verdict.overfit_flags or ()):
        flags.append("overfit_suspect")
    cost = getattr(verdict, "cost", None)
    if cost is not None and getattr(cost, "wins_by_spending", False):
        flags.append("wins_by_spending")
    return sorted(set(flags))


def cost_summary(verdict: Any) -> dict[str, Any]:
    cost = getattr(verdict, "cost", None)
    if cost is None:
        return {}
    return {
        "parent_usd_per_case": _dec(cost.parent_usd_per_case),
        "candidate_usd_per_case": _dec(cost.candidate_usd_per_case),
    }


def review_json(item: Any) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "status": item.status.value if isinstance(item.status, ReviewStatus) else str(item.status),
        "created_at": item.created_at.isoformat(),
        "decided_by": item.decided_by,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "decision_reason": item.decision_reason,
        "promotion_id": item.promotion_id,
    }


def op_items(patch: Any) -> list[dict[str, str]]:
    """从活补丁对象生成摘要条目（提交时写入实验记录）。"""

    ops = getattr(patch, "ops", None) or []
    return [
        {
            "op": str(getattr(op, "op", "")),
            "target": op_target(op),
            "tier": tier_of_op(op).value,
        }
        for op in ops
    ]
