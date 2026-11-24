"""门禁（08 §4.4）：把最终判定转成 auto_promote / needs_review / not_promotable。

切片二只做决策，不移动 `active`：revision 的完整性（全量 bot 快照）补齐之前，
真正 apply 会把 bot 覆盖成不完整状态。
"""

from __future__ import annotations

from .enums import CheckStatus, GateOutcome, RiskTier
from .models import CandidateFacts, GateCheck, GateDecision

__all__ = ["decide"]


def decide(facts: CandidateFacts, *, ceiling: RiskTier | None) -> GateDecision:
    checks: list[GateCheck] = []

    checks.append(
        GateCheck(
            "verdict",
            CheckStatus.PASS if facts.verdict_status == "accept" else CheckStatus.FAIL,
            facts.verdict_status,
        )
    )
    checks.append(
        GateCheck(
            "safety",
            CheckStatus.PASS if facts.safety_failures == 0 else CheckStatus.FAIL,
            f"{facts.safety_failures} newly failing",
        )
    )
    regression_failed = facts.regression_failures > facts.regression_tolerance
    checks.append(
        GateCheck(
            "regression",
            CheckStatus.FAIL
            if regression_failed
            else (
                CheckStatus.SOFT
                if facts.regression_failures == facts.regression_tolerance > 0
                else CheckStatus.PASS
            ),
            f"{facts.regression_failures}/{facts.regression_tolerance}",
        )
    )
    checks.append(GateCheck("budget", CheckStatus.FAIL if facts.budget_exceeded else CheckStatus.PASS, ""))

    hard_failure = any(check.status is CheckStatus.FAIL for check in checks)
    if hard_failure:
        return _decision(facts, ceiling, checks, GateOutcome.NOT_PROMOTABLE)

    # 软条件：把候选送去评审，而不是拒绝（§4.3）。
    soft = facts.frozen or facts.incident or any(check.status is CheckStatus.SOFT for check in checks)
    if soft:
        checks.append(GateCheck("soft_conditions", CheckStatus.SOFT, "review required"))
        return _decision(facts, ceiling, checks, GateOutcome.NEEDS_REVIEW)

    # T3 永不自动晋升，无论上限怎么设（§4.4）。
    within_ceiling = (
        ceiling is not None and facts.risk_tier is not RiskTier.T3 and facts.risk_tier.rank <= ceiling.rank
    )
    if within_ceiling:
        checks.append(GateCheck("risk", CheckStatus.PASS, f"{facts.risk_tier} within ceiling"))
        return _decision(facts, ceiling, checks, GateOutcome.AUTO_PROMOTE)

    checks.append(
        GateCheck(
            "risk",
            CheckStatus.SOFT,
            f"{facts.risk_tier} above ceiling {ceiling or 'none'}",
        )
    )
    return _decision(facts, ceiling, checks, GateOutcome.NEEDS_REVIEW)


def _decision(
    facts: CandidateFacts,
    ceiling: RiskTier | None,
    checks: list[GateCheck],
    outcome: GateOutcome,
) -> GateDecision:
    del ceiling  # 上限只影响结果，不单独入库
    return GateDecision(
        candidate_id=facts.candidate_id,
        owner_id="",
        bot_id="",
        run_id=None,
        outcome=outcome,
        risk_tier=facts.risk_tier,
        verdict_status=facts.verdict_status,
        checks=tuple(checks),
    )
