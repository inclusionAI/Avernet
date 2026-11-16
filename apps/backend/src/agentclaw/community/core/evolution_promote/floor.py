"""平台底线（08-promotion.zh-CN §4.2）：提交时运行的静态检查。"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from .enums import CheckName, CheckResult, FloorCheckName
from .models import GateCheck

__all__ = ["FloorChecker", "DEFAULT_FLOORS"]


class FloorChecker:
    """所有底线检查的集合；插件式，但只属于平台。"""

    def __init__(
        self, *, scanners: Sequence[Callable[[Any], list[str]]] = (), max_ops: int = 200
    ) -> None:
        self.scanners = tuple(scanners)
        self.max_ops = max_ops

    def check(
        self, patch: Any, policy: Any, allowed_genes: Sequence[str]
    ) -> tuple[GateCheck, ...]:
        return (
            self._mutable_and_pins(patch, policy),
            self._allowed_genes(patch, allowed_genes),
            self._secret_scan(patch),
            self._outbound_endpoints(patch),
            self._size(patch),
        )

    def _mutable_and_pins(self, patch: Any, policy: Any) -> GateCheck:
        from agentclaw.community.core.evolution_strategy import check_allowed_genes

        problems = check_allowed_genes(
            patch,
            list(getattr(policy, "mutable_genes", ()) or ()),
            list(getattr(policy, "pins", ()) or ()),
        )
        if problems:
            return GateCheck(
                name=FloorCheckName.MUTABLE_GENES_AND_PINS,
                result=CheckResult.FAIL,
                detail="; ".join(problems),
            )
        return GateCheck(
            name=FloorCheckName.MUTABLE_GENES_AND_PINS, result=CheckResult.PASS
        )

    def _allowed_genes(self, patch: Any, allowed_genes: Sequence[str]) -> GateCheck:
        from agentclaw.community.core.evolution_strategy import check_allowed_genes

        problems = check_allowed_genes(patch, list(allowed_genes))
        if problems:
            return GateCheck(
                name=FloorCheckName.ALLOWED_GENES,
                result=CheckResult.FAIL,
                detail="; ".join(problems),
            )
        return GateCheck(name=FloorCheckName.ALLOWED_GENES, result=CheckResult.PASS)

    def _secret_scan(self, patch: Any) -> GateCheck:
        findings: list[str] = []
        for scanner in self.scanners:
            findings.extend(scanner(patch))
        if findings:
            return GateCheck(
                name=FloorCheckName.SECRET_PII_SCAN,
                result=CheckResult.FAIL,
                detail="; ".join(findings),
            )
        return GateCheck(name=FloorCheckName.SECRET_PII_SCAN, result=CheckResult.PASS)

    def _outbound_endpoints(self, patch: Any) -> GateCheck:
        from .risk import _op_text

        for op in getattr(patch, "ops", ()):
            text = _op_text(op)
            if "://" in text and "mcp" not in text.lower():
                continue
        return GateCheck(
            name=FloorCheckName.OUTBOUND_ENDPOINTS, result=CheckResult.PASS
        )

    def _size(self, patch: Any) -> GateCheck:
        ops = list(getattr(patch, "ops", ()))
        if len(ops) > self.max_ops:
            return GateCheck(
                name=FloorCheckName.SIZE_AND_REWRITE,
                result=CheckResult.FAIL,
                detail=f"{len(ops)} ops exceeds the limit of {self.max_ops}",
            )
        rewrites = [op for op in ops if getattr(op, "rewrite", False)]
        if rewrites:
            return GateCheck(
                name=FloorCheckName.SIZE_AND_REWRITE,
                result=CheckResult.WARN,
                detail=f"{len(rewrites)} op(s) marked rewrite; tier raised to T2",
            )
        return GateCheck(name=FloorCheckName.SIZE_AND_REWRITE, result=CheckResult.PASS)


def _default_floor() -> GateCheck:
    return GateCheck(name=CheckName.FLOOR, result=CheckResult.PASS)


DEFAULT_FLOORS = FloorChecker()
