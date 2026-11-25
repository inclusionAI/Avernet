"""08 §6 / 09 §10.1：评审队列的开单、批准与拒绝。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from avernet_evolution.core.evolution_promote import (
    AlreadyDecided,
    GateDecisionStore,
    GateOutcome,
    LocalPromotionService,
    NotReviewable,
    ReviewNotFound,
    ReviewStatus,
    RiskTier,
)
from avernet_evolution.core.evolution_run.store import EvolutionStore
from avernet_evolution.core.evolution_verify import VerdictStatus
from avernet_evolution.core.evolution_verify.enums import Split
from avernet_evolution.core.evolution_verify.models import SplitResult, Verdict
from avernet_evolution.core.evolution_verify.store import VerdictStore

BOT = SimpleNamespace(owner_id="owner-1", bot_id="bot-1")


def _save(verdicts: VerdictStore, candidate: str, status: VerdictStatus) -> None:
    verdicts.save(
        Verdict(
            candidate=candidate,
            bot_owner_id=BOT.owner_id,
            bot_id=BOT.bot_id,
            run_id="run_1",
            revision="sha256:rev-" + candidate[-2:],
            parent_revision="sha256:parent",
            profile="default@1",
            status=status,
            splits={Split.REGRESSION: SplitResult(cases=2, seeds=1, mean_delta=0.0)},
        )
    )


def _service(status: VerdictStatus = VerdictStatus.ACCEPT):
    store = EvolutionStore(":memory:")
    verdicts = VerdictStore(store.conn)
    _save(verdicts, "sha256:c1", status)
    store.set_candidate_risk("sha256:c1", RiskTier.T2.value)

    return LocalPromotionService(verdicts=verdicts, store=GateDecisionStore(store.conn))


def _decide(service, status, candidate_id, *, reason, actor):
    with service.store.conn:
        service.store.conn.execute("BEGIN IMMEDIATE")
        return service.record_review(
            BOT, candidate_id, run_id="run_1", status=status, reason=reason, actor=actor
        )


@pytest.mark.asyncio
async def test_needs_review_opens_exactly_one_item() -> None:
    service = _service()
    decision = await service.decide(BOT, "sha256:c1", run_id="run_1", profile="default@1")
    assert decision.outcome is GateOutcome.NEEDS_REVIEW

    item = service.store.get_review("run_1", "sha256:c1")
    assert item is not None
    assert item.status is ReviewStatus.OPEN
    assert item.revision_id == "sha256:rev-c1"
    assert item.parent_revision_id == "sha256:parent"
    assert item.risk_tier is RiskTier.T2 and item.run_id == "run_1"

    await service.decide(BOT, "sha256:c1", run_id="run_1", profile="default@1")
    assert len(service.queue(BOT)) == 1


@pytest.mark.asyncio
async def test_auto_promotable_candidate_is_not_reviewable() -> None:
    service = _service()
    decision = await service.decide(
        BOT, "sha256:c1", run_id="run_1", profile="default@1", ceiling=RiskTier.T2
    )
    assert decision.outcome is GateOutcome.AUTO_PROMOTE
    # 没有评审单：批准路径要回 409 not_reviewable，而不是假装成功。
    with pytest.raises(NotReviewable):
        _decide(service, ReviewStatus.APPROVED, "sha256:c1", reason="looks good", actor="reviewer")
    # 完全未知的候选才是 404。
    with pytest.raises(ReviewNotFound):
        _decide(service, ReviewStatus.APPROVED, "sha256:nope", reason="looks good", actor="reviewer")


@pytest.mark.asyncio
async def test_not_promotable_candidate_cannot_be_approved() -> None:
    service = _service(status=VerdictStatus.INCONCLUSIVE)
    decision = await service.decide(BOT, "sha256:c1", run_id="run_1", profile="default@1")
    assert decision.outcome is GateOutcome.NOT_PROMOTABLE
    with pytest.raises(NotReviewable):
        _decide(service, ReviewStatus.APPROVED, "sha256:c1", reason="please", actor="reviewer")


@pytest.mark.asyncio
async def test_approve_records_the_decision_and_reports_apply_is_disabled() -> None:
    service = _service()
    await service.decide(BOT, "sha256:c1", run_id="run_1", profile="default@1")

    result = _decide(service, ReviewStatus.APPROVED, "sha256:c1", reason="diff reviewed", actor="user_17")
    assert result.review.status is ReviewStatus.APPROVED
    assert result.review.decided_by == "user_17"
    assert result.review.decision_reason == "diff reviewed"
    assert result.review.decided_at is not None
    # apply 侧未启用：不编造 promotion_id，只给出明确原因。
    assert result.review.promotion_id is None
    assert "complete state" in (result.promotion_note or "")

    with pytest.raises(AlreadyDecided):
        _decide(service, ReviewStatus.APPROVED, "sha256:c1", reason="again", actor="user_17")


@pytest.mark.asyncio
async def test_reject_closes_the_item_and_requires_a_reason() -> None:
    service = _service()
    await service.decide(BOT, "sha256:c1", run_id="run_1", profile="default@1")

    with pytest.raises(ValueError):
        _decide(service, ReviewStatus.REJECTED, "sha256:c1", reason="   ", actor="user_17")

    result = _decide(
        service, ReviewStatus.REJECTED, "sha256:c1", reason="drops the legal-hold exception", actor="user_17"
    )
    assert result.review.status is ReviewStatus.REJECTED
    assert result.review.promotion_id is None
    with pytest.raises(AlreadyDecided):
        _decide(service, ReviewStatus.REJECTED, "sha256:c1", reason="twice", actor="user_17")


@pytest.mark.asyncio
async def test_queue_filters_by_status_and_tier() -> None:
    service = _service()
    await service.decide(BOT, "sha256:c1", run_id="run_1", profile="default@1")
    # 另一个 bot 的候选不能串到本 bot 的队列里。
    other = SimpleNamespace(owner_id="owner-1", bot_id="bot-2")
    assert service.queue(other) == []

    assert [item.candidate_id for item in service.queue(BOT, status=None)] == ["sha256:c1"]
    assert service.queue(BOT, status=ReviewStatus.APPROVED) == []
    _decide(service, ReviewStatus.REJECTED, "sha256:c1", reason="not now", actor="user_17")
    assert service.queue(BOT) == []
    assert [item.status for item in service.queue(BOT, status=ReviewStatus.REJECTED)] == [
        ReviewStatus.REJECTED
    ]


@pytest.mark.asyncio
async def test_gate_and_review_commit_together_and_missing_review_can_be_repaired(monkeypatch) -> None:
    service = _service()
    run_id, candidate_id = "run_1", "sha256:c1"

    def fail(review):
        raise RuntimeError("injected review insert failure")

    with monkeypatch.context() as patch:
        patch.setattr(service.store, "_open_review", fail)
        with pytest.raises(RuntimeError, match="injected review insert failure"):
            await service.decide(BOT, candidate_id, run_id=run_id, profile="default@1")
    assert service.store.get(run_id, candidate_id) is None
    decision = await service.decide(BOT, candidate_id, run_id=run_id, profile="default@1")
    with service.store.conn:
        service.store.conn.execute("DELETE FROM evolution_review_item")
    assert await service.decide(BOT, candidate_id, run_id=run_id, profile="default@1") == decision
    assert service.store.get_review(run_id, candidate_id).status is ReviewStatus.OPEN
