import asyncio
from copy import deepcopy
import json
import pytest

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    actor_context_scope,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
    AgentRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum as A,
    StatusEnum as S,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    AdjustmentProposal,
    StagePatch,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_policy import (
    AdaptiveUntilAccepted,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_controller import (
    MultiRolloutController,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.session_messages import (
    OpenApiStageSessionMessages,
)
from .conftest import policy
from .helpers import saved, finish

pytestmark = pytest.mark.asyncio


async def waiting(h, **limits):
    await h.create(
        policy(AdaptiveUntilAccepted("interactive", "stage-agent@1", 600), **limits)
    )
    rid = saved(h).current_rollout_id
    await finish(h, rid, False)
    assert saved(h).multi.phase == "WAITING_FEEDBACK"
    return rid


async def test_feedback_new_plan_real_stage_input_pass_and_durable_uuid(multi):
    h = multi
    rid = await waiting(h)
    historical = deepcopy(saved(h).rollouts[0])
    await h.worker.run_once()
    await h.worker.run_once()
    assert len(h.calls) == 1 and len(saved(h).rollouts) == 1
    h.notifications.notify.assert_awaited_once()
    h.strategy.propose_adjustment.return_value = AdjustmentProposal(
        A.OPTIMIZE,
        "improve stage",
        (StagePatch("build", "first add a concurrency regression"),),
        ("build",),
    )
    result = await h.service.interactive(
        rid, "fix-1", "the concurrency case is missing"
    )
    snap = saved(h)
    assert result.type is A.OPTIMIZE and len(snap.rollouts) == 2
    assert snap.multi.plans[1].previous == snap.multi.plans[0].digest
    assert snap.multi.generation == 2 and snap.multi.waiting_until == 0
    assert snap.rollouts[0] == historical
    assert not (
        {s.stage_rollout_id for s in historical.stages}
        & {s.stage_rollout_id for s in snap.current().stages}
    )
    assert snap.parents[str(snap.current_rollout_id)] == rid
    assert snap.current().stages[0].session_id == ""
    # Reconstruct the service/controller, not just a list of in-memory candidates.
    controller = MultiRolloutController(
        h.repository,
        h.strategy,
        h.isolation,
        h.notifications,
        h.authorization,
        h.interaction,
    )
    service = ProductProfileService(
        h.repository,
        ProfilePublisherAllowlist((h.owner.actor,)),
        h.authorization,
        h.execution,
        h.interaction,
        OpenApiStageSessionMessages(h.bot),
        controller,
    )
    assert (
        await service.interactive(rid, "fix-1", "the concurrency case is missing")
        == result
    )
    h.strategy.propose_adjustment.assert_awaited_once()
    with pytest.raises(Conflict):
        await service.interactive(rid, "fix-1", "different feedback")
    with actor_context_scope(ActorContext(PrincipalRef("human", "outsider"), "test")):
        with pytest.raises(Forbidden):
            await service.interactive(rid, "fix-1", "the concurrency case is missing")
    new_id = snap.current_rollout_id
    await finish(h, new_id)
    execution_input = json.loads(h.calls[-1]["message"])
    assert (
        execution_input["multi_rollout"]["stage_instruction"]
        == "first add a concurrency regression"
    )
    assert (
        execution_input["multi_rollout"]["plan_digest"]
        == saved(h).multi.plans[-1].digest
    )
    assert saved(h).multi.phase == "SUCCEEDED" and saved(h).multi.winner == new_id
    assert saved(h).rollouts[0] == historical
    assert saved(h).multi.strategy_calls == 1
    with pytest.raises(Conflict):
        await service.interactive(new_id, "new-after-success", "try again")


async def test_autonomous_failure_intent_repeated_worker_only_one_successor(multi):
    h = multi
    h.strategy.autonomous_grant = True
    await h.create(policy(AdaptiveUntilAccepted("autonomous", "stage-agent@1")))
    first = saved(h).current_rollout_id
    h.strategy.propose_adjustment.return_value = AdjustmentProposal(
        A.RE_DO, "retry unchanged plan"
    )
    await finish(h, first, False)
    assert saved(h).multi.phase == "ADAPTING"
    assert len(saved(h).rollouts) == 1
    h.strategy.propose_adjustment.assert_not_awaited()
    await h.worker.run_once()
    second = saved(h).current_rollout_id
    assert second != first and len(saved(h).rollouts) == 2
    h.strategy.propose_adjustment.assert_awaited_once()
    assert len(saved(h).multi.plans) == 1
    # Stale/duplicate autonomous work cannot create generation 3.
    await h.controller.adapt(h.collet.id, h.owner)
    assert len(saved(h).rollouts) == 2
    await finish(h, second)
    assert saved(h).multi.phase == "SUCCEEDED"


@pytest.mark.parametrize(
    "proposal",
    [
        AdjustmentProposal(A.OPTIMIZE, "unknown", (StagePatch("unknown", "x"),)),
        AdjustmentProposal(
            A.OPTIMIZE,
            "expand",
            (StagePatch("build", candidates=(AgentRef("bot", "attacker"),)),),
        ),
        AdjustmentProposal(A.RE_DO, "hidden patch", (StagePatch("build", "x"),)),
        AdjustmentProposal(A.SHUTDOWN, "hidden patch", rerun=("build",)),
        AdjustmentProposal(A.OPTIMIZE, "bad scope", rerun=("unknown",)),
        AdjustmentProposal(
            A.OPTIMIZE,
            "duplicate",
            (StagePatch("build", "x"), StagePatch("build", "y")),
        ),
    ],
)
async def test_malicious_patch_preserves_profile_and_history(multi, proposal):
    h = multi
    rid = await waiting(h)
    profile_before = h.repository.get_profile(h.profile.id)[0]
    history_before = deepcopy(saved(h).rollouts)
    h.strategy.propose_adjustment.return_value = proposal
    with pytest.raises(Invalid):
        await h.service.interactive(rid, "invalid", "change target/budget")
    assert h.repository.get_profile(h.profile.id)[0] == profile_before
    assert saved(h).rollouts == history_before
    assert len(saved(h).multi.plans) == 1
    assert saved(h).multi.strategy_calls == 1


async def test_duplicate_and_distinct_uuid_inflight_never_call_agent_twice(multi):
    h = multi
    rid = await waiting(h)
    entered, release = asyncio.Event(), asyncio.Event()

    async def propose(*args):
        entered.set()
        await release.wait()
        return AdjustmentProposal(A.RE_DO, "retry")

    h.strategy.propose_adjustment.side_effect = propose
    task = asyncio.create_task(h.service.interactive(rid, "one-action", "feedback"))
    await entered.wait()
    try:
        for uuid in ("one-action", "different-action"):
            with pytest.raises(Conflict, match="in flight"):
                await h.service.interactive(rid, uuid, "feedback")
        h.strategy.propose_adjustment.assert_awaited_once()
    finally:
        release.set()
    first = await task
    assert await h.service.interactive(rid, "one-action", "feedback") == first
    assert len(saved(h).rollouts) == 2 and saved(h).multi.strategy_calls == 1


async def test_noop_preserves_absolute_wait_then_exhausts_progress(multi):
    h = multi
    rid = await waiting(h)
    deadline = saved(h).multi.waiting_until
    h.strategy.propose_adjustment.return_value = AdjustmentProposal(
        A.DO_NOTHING, "wait for better feedback"
    )
    await h.service.interactive(rid, "noop-1", "not sure")
    assert saved(h).multi.phase == "WAITING_FEEDBACK"
    assert saved(h).multi.waiting_until == deadline
    await h.service.interactive(rid, "noop-2", "still not sure")
    assert saved(h).multi.phase == "FAILED" and saved(h).multi.outcome == "NO_PROGRESS"
    assert len(h.calls) == 1


async def test_autonomous_noop_can_escalate_to_human_with_bounded_wait(multi):
    h = multi
    h.strategy.autonomous_grant = True
    await h.create(policy(AdaptiveUntilAccepted("autonomous", "stage-agent@1")))
    await finish(h, saved(h).current_rollout_id, False)
    h.strategy.propose_adjustment.return_value = AdjustmentProposal(
        A.DO_NOTHING, "need human evidence", request_feedback=True
    )
    await h.controller.adapt(h.collet.id, h.owner)
    assert saved(h).multi.phase == "WAITING_FEEDBACK"
    assert saved(h).multi.waiting_until <= saved(h).multi.deadline
    assert len(saved(h).rollouts) == 1


async def test_feedback_timeout_after_reconstruction_uses_original_deadline(
    multi, monkeypatch
):
    h = multi
    await waiting(h)
    deadline = saved(h).multi.waiting_until
    from agentclaw.community.core.task.context_collab.l5_methods import (
        multi_rollout_controller as control,
    )
    from agentclaw.community.core.task.context_collab.l6_products.persistence import (
        runtime_outbox as outbox,
    )

    monkeypatch.setattr(control, "time", lambda: deadline)
    monkeypatch.setattr(outbox, "time", lambda: deadline)
    assert await h.worker.check_deadlines() == 1
    assert (
        saved(h).multi.phase == "FAILED"
        and saved(h).multi.outcome == "FEEDBACK_TIMEOUT"
    )
    h.strategy.propose_adjustment.assert_not_awaited()


async def test_rollout_dispatch_and_strategy_budgets_are_charged_before_io(multi):
    h = multi
    rid = await waiting(h, max_strategy_calls=1)
    h.strategy.propose_adjustment.side_effect = TimeoutError("agent result unknown")
    with pytest.raises(TimeoutError):
        await h.service.interactive(rid, "retry-op", "feedback")
    assert saved(h).multi.strategy_calls == 1
    with pytest.raises(Conflict, match="budget"):
        await h.service.interactive(rid, "retry-op", "feedback")
    h.strategy.propose_adjustment.assert_awaited_once()
    assert saved(h).multi.outcome == "STRATEGY_BUDGET"


async def test_dispatch_budget_stops_before_second_external_call(multi):
    h = multi
    rid = await waiting(h, max_stage_dispatches=1)
    h.strategy.propose_adjustment.return_value = AdjustmentProposal(A.RE_DO, "retry")
    await h.service.interactive(rid, "one-retry", "feedback")
    await h.engine.advance(h.collet.id, h.owner, rollout_id=saved(h).current_rollout_id)
    assert saved(h).multi.outcome == "DISPATCH_BUDGET"
    assert len(h.calls) == 1 and saved(h).multi.dispatches == 1


async def test_last_rollout_failure_stops_without_free_extra_candidate(multi):
    h = multi
    await h.create(
        policy(
            AdaptiveUntilAccepted("interactive", "stage-agent@1", 600), max_rollouts=1
        )
    )
    await finish(h, saved(h).current_rollout_id, False)
    assert saved(h).multi.outcome == "ROLLOUT_BUDGET"
    assert len(saved(h).rollouts) == 1
    h.strategy.propose_adjustment.assert_not_awaited()


async def test_runtime_failure_is_infrastructure_not_autonomous_quality_retry(multi):
    h = multi
    h.strategy.autonomous_grant = True
    await h.create(policy(AdaptiveUntilAccepted("autonomous", "stage-agent@1")))
    rid = saved(h).current_rollout_id
    await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    h.state["value"] = "error"
    await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    assert saved(h).multi.phase == "FAILED"
    assert saved(h).multi.outcome == "INFRASTRUCTURE_RUN_FAILED"
    assert saved(h).collet.status is S.RUN_FAILED
    h.strategy.propose_adjustment.assert_not_awaited()


async def test_autonomous_delegation_rechecked_at_real_strategy_call(multi):
    h = multi
    h.strategy.autonomous_grant = True
    await h.create(policy(AdaptiveUntilAccepted("autonomous", "stage-agent@1")))
    await finish(h, saved(h).current_rollout_id, False)
    h.strategy.autonomous_grant = False
    with pytest.raises(Forbidden, match="delegation"):
        await h.controller.adapt(h.collet.id, h.owner)
    h.strategy.propose_adjustment.assert_not_awaited()
    assert len(saved(h).rollouts) == 1


async def test_delegation_revoked_during_agent_response_cannot_commit_successor(multi):
    h = multi
    h.strategy.autonomous_grant = True
    await h.create(policy(AdaptiveUntilAccepted("autonomous", "stage-agent@1")))
    await finish(h, saved(h).current_rollout_id, False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def propose(*args):
        entered.set()
        await release.wait()
        return AdjustmentProposal(A.RE_DO, "retry")

    h.strategy.propose_adjustment.side_effect = propose
    task = asyncio.create_task(h.controller.adapt(h.collet.id, h.owner))
    await entered.wait()
    h.strategy.autonomous_grant = False
    release.set()
    with pytest.raises(Forbidden, match="delegation"):
        await task
    assert len(saved(h).rollouts) == 1 and saved(h).multi.winner == 0
    assert saved(h).multi.strategy_calls == 1
