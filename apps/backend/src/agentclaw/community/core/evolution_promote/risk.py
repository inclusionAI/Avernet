"""风险分级（08-promotion.zh-CN §5.1）：按操作映射、取最大值，再应用提升。"""

from __future__ import annotations

from typing import Any, Iterable

from .enums import RaiseKind, RiskTier
from .models import OpTier, TierAssessment, TierRaise

__all__ = ["classify", "DEFAULT_RULES", "op_genes"]

#: (op, target root, condition, tier)；对每个操作取第一条命中的规则。
DEFAULT_RULES: tuple[tuple[str, str, str | None, RiskTier], ...] = (
    ("file.edit", "persona", None, RiskTier.T2),
    ("file.edit", "skills", "description_only", RiskTier.T1),
    ("file.edit", "skills", None, RiskTier.T2),
    ("file.edit", "resources", None, RiskTier.T1),
    ("persona.add", "persona", None, RiskTier.T2),
    ("persona.remove", "persona", None, RiskTier.T2),
    ("skill.add", "skills", None, RiskTier.T2),
    ("skill.update", "skills", "description_only", RiskTier.T1),
    ("skill.update", "skills", None, RiskTier.T2),
    ("skill.remove", "skills", None, RiskTier.T2),
    ("resource.add", "resources", None, RiskTier.T1),
    ("resource.remove", "resources", None, RiskTier.T1),
    ("memory.add", "memory", None, RiskTier.T1),
    ("memory.update", "memory", None, RiskTier.T1),
    ("memory.retire", "memory", None, RiskTier.T1),
    ("memory.set_mode", "memory", "mode=replace", RiskTier.T3),
    ("memory.set_mode", "memory", None, RiskTier.T1),
)

_GUARDRAIL_MARKERS = (
    "guardrail",
    "refusal",
    "refuse to",
    "safety section",
    "report to",
    "escalate to",
    "guardrail:",
    "评测指令",
    "拒答",
    "安全章节",
)


def op_genes(op: Any) -> tuple[str, ...]:
    """一个操作触及的基因路径（与 03 的 allowed_genes 检查同源）。"""

    kind = getattr(op, "op", "")
    if kind == "file.edit":
        return (op.target,)
    if kind in ("persona.add", "persona.remove"):
        return (f"persona/{op.type}",)
    if kind in ("skill.add", "skill.update", "skill.remove"):
        return (f"skills/{op.name}/",)
    if kind in ("resource.add", "resource.remove"):
        return (op.path,)
    if getattr(op, "item", None) is not None:
        return (f"memory/{op.item.key}",)
    if kind == "memory.retire":
        return (f"memory/{op.key}",)
    return ()


def _root(gene: str) -> str:
    return gene.split("/", 1)[0]


def _condition_holds(op: Any, condition: str | None) -> bool:
    if condition is None:
        return True
    if condition == "description_only":
        return bool(getattr(op, "description_only", False))
    if condition.startswith("mode="):
        return getattr(op, "mode", None) == condition.split("=", 1)[1]
    return False


def _tier_for(op: Any, gene: str) -> tuple[RiskTier, str | None]:
    kind = getattr(op, "op", "")
    root = _root(gene)
    for rule_op, rule_root, condition, tier in DEFAULT_RULES:
        if rule_op != kind or rule_root != root:
            continue
        if _condition_holds(op, condition):
            return tier, condition
    return RiskTier.T2, None


def classify(patch: Any, policy: Any = None) -> TierAssessment:
    """补丁的风险等级：每个操作一条，取最大值；随后应用提升。"""

    per_op: list[OpTier] = []
    tier = RiskTier.T0
    for index, op in enumerate(getattr(patch, "ops", ())):
        genes = op_genes(op)
        op_tier = RiskTier.T0
        condition: str | None = None
        for gene in genes:
            candidate_tier, candidate_condition = _tier_for(op, gene)
            if candidate_tier > op_tier:
                op_tier, condition = candidate_tier, candidate_condition
        per_op.append(
            OpTier(
                index=index,
                op=getattr(op, "op", "?"),
                genes=genes,
                tier=op_tier,
                condition=condition,
            )
        )
        tier = max(tier, op_tier)

    raises: list[TierRaise] = []
    tier, raises = _apply_raises(patch, tier, raises)
    tier, raises = _apply_owner_overrides(patch, policy, tier, raises)
    return TierAssessment(tier=tier, per_op=tuple(per_op), raises=tuple(raises))


def _apply_raises(
    patch: Any, tier: RiskTier, raises: list[TierRaise]
) -> tuple[RiskTier, list[TierRaise]]:
    for op in getattr(patch, "ops", ()):
        if getattr(op, "rewrite", False) or getattr(op, "marked_rewrite", False):
            raised = max(tier, RiskTier.T2)
            if raised > tier:
                raises.append(
                    TierRaise(
                        RaiseKind.REWRITE,
                        f"{getattr(op, 'op', '?')} rewrites a file",
                        tier,
                        raised,
                    )
                )
                tier = raised
        text = _op_text(op)
        if text and any(marker in text.lower() for marker in _GUARDRAIL_MARKERS):
            raised = max(tier, RiskTier.T2)
            if raised > tier:
                raises.append(
                    TierRaise(
                        RaiseKind.GUARDRAIL_TOUCH,
                        "content mentions guardrail text",
                        tier,
                        raised,
                    )
                )
                tier = raised
    return tier, raises


def _apply_owner_overrides(
    patch: Any, policy: Any, tier: RiskTier, raises: list[TierRaise]
) -> tuple[RiskTier, list[TierRaise]]:
    overrides: Iterable[Any] = getattr(policy, "risk_overrides", ()) or ()
    for override in overrides:
        path = (
            getattr(override, "path", None)
            if not isinstance(override, dict)
            else override.get("path")
        )
        min_tier = (
            getattr(override, "min_tier", None)
            if not isinstance(override, dict)
            else override.get("min_tier")
        )
        if not path or min_tier is None:
            continue
        for op in getattr(patch, "ops", ()):
            if any(
                gene == path or gene.startswith(path.rstrip("/") + "/")
                for gene in op_genes(op)
            ):
                raised = max(tier, RiskTier.parse(min_tier) or RiskTier.T2)
                if raised > tier:
                    raises.append(
                        TierRaise(
                            RaiseKind.OWNER_OVERRIDE,
                            f"policy override on {path}",
                            tier,
                            raised,
                        )
                    )
                    tier = raised
                break
    return tier, raises


def _op_text(op: Any) -> str:
    for attribute in ("content", "text", "body"):
        value = getattr(op, attribute, None)
        if isinstance(value, str):
            return value
    item = getattr(op, "item", None)
    if item is not None and isinstance(getattr(item, "text", None), str):
        return item.text
    return ""
