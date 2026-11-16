"""门禁（08-promotion.zh-CN §4.1–§4.4）：纯函数。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .enums import CheckName, CheckResult, GateOutcome, RiskTier
from .models import GateCheck, GateDecision, TierAssessment

__all__ = ["GateInput", "decide"]


@dataclass(frozen=True)
class GateInput:
    candidate: str
    verdict: str
    risk: TierAssessment
    floor: tuple[GateCheck, ...] = ()
    regression_newly_failing: int = 0
    regression_tolerance: int = 1
    safety_newly_failing: int = 0
    holdout_drop: bool = False
    baseline_ok: bool = True
    baseline_required: bool = False
    auto_promote_ceiling: RiskTier | None = RiskTier.T1
    incidents_open: bool = False
    frozen: bool = False
    daily_limit_reached: bool = False
    decided_at: datetime | None = None


def decide(gate_input: GateInput) -> GateDecision:
    """§4.4：检查 1–5 决定可晋升性，检查 6 决定是否需要人。"""

    checks: list[GateCheck] = list(gate_input.floor)
    checks.append(_regression_floor(gate_input))
    checks.append(_verdict_check(gate_input))
    checks.append(_holdout_check(gate_input))
    checks.append(_baseline_check(gate_input))

    failed = (
        any(_result(check) is CheckResult.FAIL for check in checks)
        or gate_input.verdict != "accept"
    )
    if failed:
        return GateDecision(
            candidate=gate_input.candidate,
            verdict=gate_input.verdict,
            risk=gate_input.risk,
            checks=tuple(checks),
            outcome=GateOutcome.NOT_PROMOTABLE,
            auto_promote_ceiling=gate_input.auto_promote_ceiling,
            decided_at=gate_input.decided_at or _now(),
        )

    soft = _soft_reasons(gate_input)
    ceiling = gate_input.auto_promote_ceiling
    within = (
        ceiling is not None
        and gate_input.risk.tier <= ceiling
        and gate_input.risk.tier < RiskTier.T3
    )
    approval = GateCheck(
        name=CheckName.RISK_TIER_APPROVAL,
        result=CheckResult.PASS if (within and not soft) else CheckResult.FAIL,
        detail="" if within and not soft else _why(within, soft),
    )
    checks.append(approval)
    outcome = (
        GateOutcome.AUTO_PROMOTE if (within and not soft) else GateOutcome.NEEDS_REVIEW
    )
    return GateDecision(
        candidate=gate_input.candidate,
        verdict=gate_input.verdict,
        risk=gate_input.risk,
        checks=tuple(checks),
        outcome=outcome,
        auto_promote_ceiling=ceiling,
        decided_at=gate_input.decided_at or _now(),
        detail="; ".join(soft),
    )


def _regression_floor(gate_input: GateInput) -> GateCheck:
    if gate_input.safety_newly_failing:
        return GateCheck(
            CheckName.REGRESSION_FLOOR,
            CheckResult.FAIL,
            f"{gate_input.safety_newly_failing} new safety failure(s); no tolerance",
        )
    if gate_input.regression_newly_failing > gate_input.regression_tolerance:
        return GateCheck(
            CheckName.REGRESSION_FLOOR,
            CheckResult.FAIL,
            f"{gate_input.regression_newly_failing} new regression failure(s) "
            f"beyond tolerance {gate_input.regression_tolerance}",
        )
    if gate_input.regression_newly_failing:
        return GateCheck(
            CheckName.REGRESSION_FLOOR,
            CheckResult.WARN,
            "regression tolerance spent; routed to review",
        )
    return GateCheck(CheckName.REGRESSION_FLOOR, CheckResult.PASS)


def _verdict_check(gate_input: GateInput) -> GateCheck:
    result = CheckResult.PASS if gate_input.verdict == "accept" else CheckResult.FAIL
    return GateCheck(CheckName.VERDICT, result, f"verdict is {gate_input.verdict}")


def _holdout_check(gate_input: GateInput) -> GateCheck:
    if gate_input.holdout_drop:
        return GateCheck(CheckName.HOLDOUT, CheckResult.FAIL, "holdout audit degraded")
    return GateCheck(CheckName.HOLDOUT, CheckResult.PASS, "no holdout regression")


def _baseline_check(gate_input: GateInput) -> GateCheck:
    if gate_input.baseline_required and not gate_input.baseline_ok:
        return GateCheck(
            CheckName.BASELINE,
            CheckResult.FAIL,
            "candidate does not beat a cost-matched baseline",
        )
    return GateCheck(CheckName.BASELINE, CheckResult.PASS)


def _soft_reasons(gate_input: GateInput) -> list[str]:
    reasons: list[str] = []
    if (
        gate_input.regression_newly_failing
        and gate_input.regression_newly_failing <= gate_input.regression_tolerance
    ):
        reasons.append("tolerated regression failure")
    if gate_input.incidents_open:
        reasons.append("holdout incident open on this bot")
    if gate_input.frozen:
        reasons.append("evolution is frozen for this bot")
    if gate_input.daily_limit_reached:
        reasons.append("daily promotion limit reached")
    return reasons


def _why(within: bool, soft: list[str]) -> str:
    if soft:
        return "; ".join(soft)
    return "risk tier above the auto-promote ceiling" if not within else ""


def _result(check: GateCheck) -> CheckResult:
    return (
        check.result
        if isinstance(check.result, CheckResult)
        else CheckResult(check.result)
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
