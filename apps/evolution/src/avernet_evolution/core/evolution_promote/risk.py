"""风险分级（08 §5.1）：按补丁操作分配，补丁取最大值。

这是规范表的最小可执行投影；`description_only` 等条件需要 diff 语义，暂时按
保守方向取更严的等级——保守只会让候选多走一次人工评审，不会让它绕过评审。
"""

from __future__ import annotations

from typing import Any

from .enums import RiskTier

__all__ = ["classify", "tier_of_op"]

#: op → 等级；未列出的 op 视为 T2（未知即需要人看）。
_TIERS: dict[str, RiskTier] = {
    "file.edit": RiskTier.T2,
    "persona.add": RiskTier.T2,
    "persona.remove": RiskTier.T2,
    "skill.add": RiskTier.T2,
    "skill.update": RiskTier.T2,
    "skill.remove": RiskTier.T2,
    "resource.add": RiskTier.T1,
    "resource.remove": RiskTier.T1,
    "memory.add": RiskTier.T1,
    "memory.update": RiskTier.T1,
    "memory.retire": RiskTier.T1,
    "memory.set_mode": RiskTier.T1,
}


def tier_of_op(op: Any) -> RiskTier:
    kind = getattr(op, "op", None) or (op.get("op") if isinstance(op, dict) else None)
    if kind == "memory.set_mode":
        mode = getattr(op, "mode", None) or (op.get("mode") if isinstance(op, dict) else None)
        value = getattr(mode, "value", mode)
        # §5.1：记忆 replace 模式是 T3，永不自动晋升。
        return RiskTier.T3 if value == "replace" else RiskTier.T1
    target = getattr(op, "target", None) or (op.get("target") if isinstance(op, dict) else None)
    if kind == "file.edit" and isinstance(target, str):
        if target.startswith("resources/"):
            return RiskTier.T1
        if target.startswith("persona/"):
            return RiskTier.T2
    return _TIERS.get(str(kind), RiskTier.T2)


def classify(patch: Any) -> RiskTier:
    ops = getattr(patch, "ops", None)
    if ops is None and isinstance(patch, dict):
        ops = patch.get("ops") or []
    tiers = [tier_of_op(op) for op in (ops or [])]
    if not tiers:
        return RiskTier.T0
    return max(tiers, key=lambda tier: tier.rank)
