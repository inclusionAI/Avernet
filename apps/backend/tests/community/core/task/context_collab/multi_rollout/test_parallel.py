import asyncio
from dataclasses import replace
import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum as S,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_policy import (
    ParallelBestOf,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    CandidateScore,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductCandidateSlotRow,
)
from .conftest import policy
from .helpers import saved, finish, report

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("repeat", range(3))
async def test_four_candidates_two_overlapping_real_calls_and_stable_winner(
    multi, repeat
):
    h = await multi.create()
    ids = [r.rollout_id for r in saved(h).rollouts]
    initial_focus = saved(h).current_rollout_id
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.bot.send_message.side_effect
    active, peak = 0, 0

    async def blocking(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        await release.wait()
        try:
            return await original(**kwargs)
        finally:
            active -= 1

    h.bot.send_message.side_effect = blocking
    pumping = asyncio.create_task(h.worker.run_once())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert active == peak == 2  # Provider calls really overlap, not just Tasks.
        with h.repository.db.orm_session() as session:
            assert session.query(ProductCandidateSlotRow).count() == 2
    finally:
        release.set()
        await asyncio.wait_for(pumping, 15)
    h.bot.send_message.side_effect = original
    started = [v.rollout_id for v in saved(h).multi.candidates if v.started]
    assert len(started) == 2
    for rid in reversed(started):
        await finish(h, rid, passed=rid != started[0])
    assert saved(h).multi.winner == 0
    assert saved(h).collet.status is S.RUNNING
    assert saved(h).current_rollout_id == initial_focus
    remaining = [rid for rid in ids if rid not in started]
    for rid in remaining:
        await finish(h, rid)
    assert saved(h).multi.phase == "SELECTING"
    # Equal scores choose declaration order, NOT completion/dispatch order.
    await h.worker.run_once()
    result = saved(h)
    qualified = [
        r.rollout_id for r in result.rollouts if r.status is S.ACCEPTANCE_SUCCEED
    ]
    assert result.multi.winner == qualified[0]
    assert result.current_rollout_id == qualified[0]
    assert result.collet.status is S.ACCEPTANCE_SUCCEED
    assert len({r.stages[0].session_id for r in result.rollouts}) == 4
    assert len({v.binding.method_binding_id for v in result.multi.candidates}) == 4
    assert len({v.namespace for v in result.multi.candidates}) == 4
    for candidate in result.multi.candidates:
        assert (h.isolation.directory / candidate.namespace / "result.txt").exists()
    assert len(h.calls) == 4
    assert result.multi.peak_concurrency == 2
    assert result.multi.dispatches == 4 and result.multi.strategy_calls == 1
    await h.worker.run_once()
    h.strategy.evaluate.assert_awaited_once()
    query = await h.service.query_collet_rollout(h.collet.id)
    assert query.multi_rollout_control["completed"] == 4
    assert query.multi_rollout_control["accepted"] == 3
    assert len(await h.service.query_collet_rollout(h.collet.id, False)) == 1


async def test_no_accepted_candidate_is_not_a_winner(multi):
    h = await multi.create(policy(ParallelBestOf(2, 2, "highest-score@1")))
    for rid in [r.rollout_id for r in saved(h).rollouts]:
        await finish(h, rid, False)
    assert saved(h).multi.outcome == "NO_ACCEPTED_CANDIDATE"
    assert saved(h).collet.status is S.ACCEPTANCE_FAILED
    h.strategy.evaluate.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid",
    [
        "nan",
        "infinity",
        "missing",
        "duplicate",
        "unknown",
        "range",
        "bool",
        "huge_integer",
    ],
)
async def test_untrusted_or_invalid_scores_fail_closed(multi, invalid):
    h = await multi.create(policy(ParallelBestOf(2, 2, "highest-score@1")))
    ids = [r.rollout_id for r in saved(h).rollouts]
    for rid in ids:
        await finish(h, rid)
    scores = (
        CandidateScore(ids[0], 50, "trusted"),
        CandidateScore(ids[1], 50, "trusted"),
    )
    bad = dict(
        nan=float("nan"),
        infinity=float("inf"),
        range=101,
        bool=True,
        huge_integer=10**400,
    )
    if invalid in bad:
        scores = (replace(scores[0], score=bad[invalid]), scores[1])
    elif invalid == "missing":
        scores = scores[:1]
    elif invalid == "duplicate":
        scores = (scores[0], scores[0])
    else:
        scores = (scores[0], replace(scores[1], rollout_id=999999))
    h.strategy.evaluate.side_effect = None
    h.strategy.evaluate.return_value = scores
    with pytest.raises(Invalid):
        await h.controller.select(h.collet.id, h.owner)
    assert saved(h).multi.winner == 0
    assert saved(h).multi.phase == "FAILED"


async def test_two_nonfocus_progress_reports_keep_aggregate_cas_semantics(multi):
    h = await multi.create(policy(ParallelBestOf(2, 2, "highest-score@1")))
    ids = [r.rollout_id for r in saved(h).rollouts]
    h.state["value"] = "final"
    for rid in ids:
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    stages = [r.stages[0] for r in saved(h).rollouts]
    one, ctx1 = report(h, stages[0])
    two, ctx2 = report(h, stages[1])
    first = await h.engine.report(one, ctx1)
    with pytest.raises(Conflict):
        await h.engine.report(two, ctx2)
    assert await h.engine.report(one, ctx1) == first
    # Confirm stale request did not commit, refresh revision AND technical ID.
    two, retry = report(h, stages[1])
    await h.engine.report(two, retry)
    assert all(r.status is S.ACCEPTANCE_SUCCEED for r in saved(h).rollouts)
    assert saved(h).multi.phase == "SELECTING"


async def test_two_collets_share_atomic_tenant_admission(multi):
    h = await multi.create(policy(ParallelBestOf(2, 2, "highest-score@1")))
    first = h.collet
    second = await h.service.create_product_collet(h.profile.id, "another input")
    snapshots = [h.repository.get_collet(c.id) for c in (first, second)]
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.bot.send_message.side_effect
    active, peak = 0, 0

    async def blocking(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            entered.set()
        await release.wait()
        try:
            return await original(**kwargs)
        finally:
            active -= 1

    h.bot.send_message.side_effect = blocking
    tasks = [
        asyncio.create_task(
            h.engine.advance(s.collet.id, h.owner, rollout_id=r.rollout_id)
        )
        for s in snapshots
        for r in s.rollouts
    ]
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert active == 2
    finally:
        release.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    assert peak <= 2
    assert sum(isinstance(v, Conflict) for v in outcomes) == 2
    with h.repository.db.orm_session() as session:
        assert session.query(ProductCandidateSlotRow).count() == 2


async def test_owner_acceptance_stays_independent(multi):
    h = await multi.create(
        policy(ParallelBestOf(1, 1, "highest-score@1")), overall="owner"
    )
    await finish(h, saved(h).current_rollout_id)
    await h.controller.select(h.collet.id, h.owner)
    assert saved(h).multi.phase == "SUCCEEDED"
    assert saved(h).collet.status is S.RUN_FINISHED


async def test_owner_authority_revoked_during_evaluation_does_not_adopt(multi):
    from agentclaw.community.core.errors import Forbidden

    h = await multi.create(policy(ParallelBestOf(1, 1, "highest-score@1")))
    await finish(h, saved(h).current_rollout_id)
    entered, release = asyncio.Event(), asyncio.Event()
    original = h.strategy.evaluate.side_effect

    async def evaluate(*args):
        result = await original(*args)
        entered.set()
        await release.wait()
        return result

    h.strategy.evaluate.side_effect = evaluate
    task = asyncio.create_task(h.controller.select(h.collet.id, h.owner))
    await entered.wait()
    check = h.authorization.check

    def revoked(action, context, owner):
        if action == "advance":
            raise Forbidden("adoption authority revoked")
        check(action, context, owner)

    h.authorization.check = revoked
    release.set()
    with pytest.raises(Forbidden, match="revoked"):
        await task
    assert saved(h).multi.winner == 0 and saved(h).multi.strategy_calls == 1
