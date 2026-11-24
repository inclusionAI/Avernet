"""08 切片二：风险分级与门禁决策（不 apply）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from avernet_evolution.core.evolution_promote import (
    CandidateFacts,
    GateDecisionStore,
    GateOutcome,
    LocalPromotionService,
    PromotionNotReady,
    RiskTier,
    VerdictPending,
    classify,
    decide,
    gate_decision_id,
)
from avernet_evolution.core.evolution_run.store import EvolutionStore
from avernet_evolution.core.evolution_verify import VerdictStatus
from avernet_evolution.core.evolution_verify.enums import Split
from avernet_evolution.core.evolution_verify.models import SplitResult, Verdict
from avernet_evolution.core.evolution_verify.store import VerdictStore

BOT = SimpleNamespace(owner_id="owner-1", bot_id="bot-1")


def _facts(**overrides) -> CandidateFacts:
    base = {
        "candidate_id": "sha256:c1",
        "risk_tier": RiskTier.T2,
        "verdict_status": "accept",
    }
    base.update(overrides)
    return CandidateFacts(**base)


def test_persona_edit_is_t2_and_resource_is_t1() -> None:
    persona = SimpleNamespace(op="file.edit", target="persona/SOUL.md")
    resource = SimpleNamespace(op="file.edit", target="resources/notes.md")
    replace_mode = SimpleNamespace(op="memory.set_mode", mode="replace")
    assert classify(SimpleNamespace(ops=[persona])) is RiskTier.T2
    assert classify(SimpleNamespace(ops=[persona, resource])) is RiskTier.T2
    assert classify(SimpleNamespace(ops=[resource])) is RiskTier.T1
    assert classify(SimpleNamespace(ops=[replace_mode])) is RiskTier.T3
    assert classify(SimpleNamespace(ops=[])) is RiskTier.T0


def test_accept_below_ceiling_auto_promotes() -> None:
    decision = decide(_facts(risk_tier=RiskTier.T1), ceiling=RiskTier.T1)
    assert decision.outcome is GateOutcome.AUTO_PROMOTE


def test_accept_above_ceiling_needs_review() -> None:
    decision = decide(_facts(risk_tier=RiskTier.T2), ceiling=RiskTier.T1)
    assert decision.outcome is GateOutcome.NEEDS_REVIEW
    assert decision.promotable


def test_t3_never_auto_promotes_even_at_ceiling() -> None:
    decision = decide(_facts(risk_tier=RiskTier.T3), ceiling=RiskTier.T3)
    assert decision.outcome is GateOutcome.NEEDS_REVIEW


def test_reject_and_safety_failure_are_not_promotable() -> None:
    assert decide(_facts(verdict_status="reject"), ceiling=None).outcome is GateOutcome.NOT_PROMOTABLE
    assert decide(_facts(safety_failures=1), ceiling=RiskTier.T2).outcome is GateOutcome.NOT_PROMOTABLE
    assert decide(_facts(budget_exceeded=True), ceiling=RiskTier.T2).outcome is GateOutcome.NOT_PROMOTABLE


def test_tolerated_regression_sends_to_review() -> None:
    decision = decide(_facts(regression_failures=1, regression_tolerance=1), ceiling=RiskTier.T2)
    assert decision.outcome is GateOutcome.NEEDS_REVIEW
    assert any(check.name == "soft_conditions" for check in decision.checks)


@pytest.mark.asyncio
async def test_service_decides_once_and_refuses_to_apply() -> None:
    store = EvolutionStore(":memory:")
    verdicts = VerdictStore(store.conn)
    verdicts.save(
        Verdict(
            candidate="sha256:c1",
            bot_owner_id="owner-1",
            bot_id="bot-1",
            run_id="run_1",
            revision="sha256:rev",
            parent_revision="sha256:parent",
            profile="default@1",
            status=VerdictStatus.ACCEPT,
            splits={Split.REGRESSION: SplitResult(cases=3, seeds=1, mean_delta=0.0)},
        )
    )
    store.set_candidate_risk("sha256:c1", RiskTier.T2.value)

    async def ceiling(bot):
        return RiskTier.T1

    service = LocalPromotionService(
        verdicts=verdicts, store=GateDecisionStore(store.conn), ceiling_for=ceiling
    )
    decision = await service.decide(BOT, "sha256:c1")
    assert decision.outcome is GateOutcome.NEEDS_REVIEW
    assert decision.owner_id == "owner-1" and decision.run_id == "run_1"

    # 幂等：再问一次不会重新计算，也不会出现第二条。
    assert await service.decide(BOT, "sha256:c1") == decision
    # needs_review 会开一张评审单；队列读的是评审条目，不是门禁记录本身。
    queued = service.queue(BOT)
    assert [item.candidate_id for item in queued] == ["sha256:c1"]
    assert queued[0].gate_decision_id == gate_decision_id("sha256:c1")
    assert service.queue(BOT, status=None)[0].candidate_id == "sha256:c1"

    with pytest.raises(PromotionNotReady):
        await service.promote(BOT, "sha256:c1")


@pytest.mark.asyncio
async def test_pending_verdict_blocks_the_gate() -> None:
    store = EvolutionStore(":memory:")
    verdicts = VerdictStore(store.conn)
    verdicts.save(
        Verdict(
            candidate="sha256:c2",
            bot_owner_id="owner-1",
            bot_id="bot-1",
            run_id="run_2",
            revision="sha256:rev2",
            parent_revision="sha256:parent",
            profile="default@1",
            status=VerdictStatus.PENDING,
        )
    )
    service = LocalPromotionService(verdicts=verdicts, store=GateDecisionStore(store.conn))
    with pytest.raises(VerdictPending):
        await service.decide(BOT, "sha256:c2")
