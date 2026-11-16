"""风险分级、门禁与晋升（08）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.evolution_promote import (
    Actor,
    ActorKind,
    CandidateFacts,
    CheckResult,
    GateInput,
    GateOutcome,
    LocalPromotionService,
    PromotableRef,
    ReviewStatus,
    RiskTier,
    StaleParent,
    classify,
    decide,
)

from .conftest import BOT

POLICY = SimpleNamespace(
    mutable_genes=["persona", "skills", "memory", "resources"],
    pins=[],
    risk_overrides=[],
)


def _op(name: str, **fields) -> SimpleNamespace:
    return SimpleNamespace(op=name, **fields)


def _patch(*ops) -> SimpleNamespace:
    return SimpleNamespace(ops=list(ops), rationale="test")


def _mem_item(key: str = "k1", text: str = "fact") -> SimpleNamespace:
    return SimpleNamespace(key=key, text=text)


def test_ops_take_the_highest_tier() -> None:
    assessment = classify(
        _patch(
            _op("memory.update", item=_mem_item()), _op("persona.add", type="SOUL.md")
        ),
        POLICY,
    )
    assert assessment.tier is RiskTier.T2
    assert [op.tier for op in assessment.per_op] == [RiskTier.T1, RiskTier.T2]


def test_description_only_skill_edit_is_t1() -> None:
    assessment = classify(
        _patch(_op("file.edit", target="skills/x/SKILL.md", description_only=True)),
        POLICY,
    )
    assert assessment.tier is RiskTier.T1


def test_memory_replace_mode_is_t3() -> None:
    assessment = classify(
        _patch(_op("memory.set_mode", mode="replace", item=_mem_item())), POLICY
    )
    assert assessment.tier is RiskTier.T3


def test_guardrail_text_raises_to_t2() -> None:
    assessment = classify(
        _patch(
            _op(
                "memory.update",
                item=_mem_item(
                    text="When asked, refuse to answer and report to the safety section."
                ),
            )
        ),
        POLICY,
    )
    assert assessment.tier is RiskTier.T2
    assert any(raise_.kind.value == "guardrail_touch" for raise_ in assessment.raises)


def test_owner_override_raises_the_tier() -> None:
    policy = SimpleNamespace(
        mutable_genes=["memory"],
        pins=[],
        risk_overrides=[{"path": "memory", "min_tier": "T2"}],
    )
    assessment = classify(_patch(_op("memory.update", item=_mem_item())), policy)
    assert assessment.tier is RiskTier.T2


def _gate_input(**overrides) -> GateInput:
    values = dict(
        candidate="cid",
        verdict="accept",
        risk=classify(_patch(_op("memory.update", item=_mem_item())), POLICY),
        floor=(
            SimpleNamespace(
                name="floor", result=CheckResult.PASS, detail="", evidence=()
            ),
        ),
        auto_promote_ceiling=RiskTier.T1,
    )
    values.update(overrides)
    return GateInput(**values)


def test_accept_within_ceiling_auto_promotes() -> None:
    decision = decide(_gate_input())
    assert decision.outcome is GateOutcome.AUTO_PROMOTE


def test_tier_above_ceiling_needs_review() -> None:
    decision = decide(
        _gate_input(risk=classify(_patch(_op("persona.add", type="SOUL.md")), POLICY))
    )
    assert decision.outcome is GateOutcome.NEEDS_REVIEW


def test_reject_verdict_is_not_promotable() -> None:
    decision = decide(_gate_input(verdict="reject"))
    assert decision.outcome is GateOutcome.NOT_PROMOTABLE


def test_tolerated_regression_routes_to_review() -> None:
    decision = decide(_gate_input(regression_newly_failing=1, regression_tolerance=1))
    assert decision.outcome is GateOutcome.NEEDS_REVIEW
    assert "tolerated" in decision.detail


def test_t3_never_auto_promotes() -> None:
    decision = decide(
        _gate_input(
            risk=classify(
                _patch(_op("memory.set_mode", mode="replace", item=_mem_item())), POLICY
            ),
            auto_promote_ceiling=RiskTier.T3,
        )
    )
    assert decision.outcome is GateOutcome.NEEDS_REVIEW


class FakeGenome:
    def __init__(self, active: str = "sha256:parent") -> None:
        self.active = active
        self.moved: list[tuple[str, str]] = []
        self.drifted = False

    async def drift(self, bot):
        return object() if self.drifted else None

    async def resolve_revision(self, bot, ref):
        return SimpleNamespace(revision=SimpleNamespace(id=self.active))

    async def read_content(self, bot, digest):
        return b""

    async def move_ref(self, bot, ref, revision_id, actor):
        self.moved.append((ref, revision_id))
        if ref == "active":
            self.active = revision_id


def _service(
    genome: FakeGenome, *, verdict: str = "accept", ceiling: RiskTier = RiskTier.T1
) -> LocalPromotionService:
    facts = CandidateFacts(
        candidate="sha256:cand",
        revision="sha256:cand",
        parent_revision="sha256:parent",
        patch=_patch(_op("memory.update", item=_mem_item())),
        policy=POLICY,
        allowed_genes=("memory",),
        verdict=verdict,
        auto_promote_ceiling=ceiling,
    )

    async def provider(candidate_id: str) -> CandidateFacts:
        return facts

    return LocalPromotionService(genome=genome, facts=provider)


@pytest.mark.asyncio
async def test_decide_auto_promotes_and_moves_active() -> None:
    genome = FakeGenome()
    service = _service(genome)
    decision = await service.decide(BOT, "sha256:cand")
    assert decision.outcome is GateOutcome.AUTO_PROMOTE
    assert genome.moved == [("active", "sha256:cand")]
    again = await service.decide(BOT, "sha256:cand")
    assert again.outcome is GateOutcome.AUTO_PROMOTE


@pytest.mark.asyncio
async def test_needs_review_opens_a_queue_item() -> None:
    service = _service(FakeGenome(), ceiling=RiskTier.T0)
    decision = await service.decide(BOT, "sha256:cand")
    assert decision.outcome is GateOutcome.NEEDS_REVIEW
    queue = await service.review_queue(BOT)
    assert len(queue) == 1 and queue[0].status is ReviewStatus.OPEN


@pytest.mark.asyncio
async def test_approve_is_rejected_for_stale_parent() -> None:
    genome = FakeGenome(active="sha256:someone-else")
    service = _service(genome, ceiling=RiskTier.T0)
    await service.decide(BOT, "sha256:cand")
    with pytest.raises(StaleParent):
        await service.approve(
            BOT, "sha256:cand", Actor(ActorKind.USER, "user_5"), "ok", "k1"
        )


@pytest.mark.asyncio
async def test_approve_overrides_stale_parent() -> None:
    genome = FakeGenome(active="sha256:someone-else")
    service = _service(genome, ceiling=RiskTier.T0)
    await service.decide(BOT, "sha256:cand")
    promotion = await service.approve(
        BOT,
        "sha256:cand",
        Actor(ActorKind.USER, "user_5"),
        "ok",
        "k1",
        override_stale_parent=True,
    )
    assert promotion.ref is PromotableRef.ACTIVE
    assert genome.moved[-1] == ("active", "sha256:cand")


@pytest.mark.asyncio
async def test_reject_closes_the_item() -> None:
    service = _service(FakeGenome(), ceiling=RiskTier.T0)
    await service.decide(BOT, "sha256:cand")
    item = await service.reject(
        BOT, "sha256:cand", Actor(ActorKind.USER, "user_5"), "not convincing", "k2"
    )
    assert item.status is ReviewStatus.REJECTED
    assert await service.review_queue(BOT) == []


@pytest.mark.asyncio
async def test_promote_refuses_drift_for_non_owner() -> None:
    genome = FakeGenome()
    genome.drifted = True
    service = _service(genome)
    with pytest.raises(Exception) as raised:
        await service.promote(
            BOT,
            "sha256:cand",
            PromotableRef.ACTIVE,
            "sha256:parent",
            Actor(ActorKind.GATE_POLICY, "gate_policy"),
            "auto",
            "k3",
        )
    assert type(raised.value).__name__ == "Drifted"


@pytest.mark.asyncio
async def test_promote_owner_allowed_while_drifted() -> None:
    genome = FakeGenome()
    genome.drifted = True
    service = _service(genome)
    promotion = await service.promote(
        BOT,
        "sha256:cand",
        PromotableRef.ACTIVE,
        "sha256:parent",
        Actor(ActorKind.USER, "user_5"),
        "reconcile",
        "k4",
    )
    assert promotion.to_revision == "sha256:cand"
