"""Public creation owns static Swarm + human-gated Agent adaptation defaults."""

from copy import deepcopy

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabVersionConflict as Conflict,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum as A,
    StatusEnum as S,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_capabilities import (
    UnavailableMultiRolloutStrategy,
    UnavailableMultiRolloutIsolation,
    UnavailableMultiRolloutNotifications,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    AdjustmentProposal,
    StagePatch,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_policy import (
    AdaptiveUntilAccepted,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_models import (
    ProductConfigurationRow,
    ProductIdentityRow,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    default_composition,
    decode_profile,
    encode_profile,
)
from .helpers import finish, report, saved

STAGES = """stages:
  - name: build
    candidates: [{type: bot, uuid: worker-a}]
    depends_on: []
    acceptance: {condition: tests pass}
"""


async def create_default(h, stages=STAGES):
    h.profile = await h.service.create_product_profile("default-coding", stages)
    h.collet = await h.service.create_product_collet(h.profile.id, "build a feature")
    return saved(h).current_rollout_id


def test_default_composition_is_exact_interactive_multi_nest_static_swarm():
    composition = default_composition()
    root, body = composition.required_methods
    assert (root.profile.method_kind, root.profile.profile_version) == (
        "MultiRollout",
        "1.0",
    )
    assert (body.profile.method_kind, body.profile.profile_version) == ("Swarm", "0.2")
    assert type(root.policy.strategy) is AdaptiveUntilAccepted
    assert root.policy.strategy.feedback_mode == "interactive"
    assert root.policy.strategy.adaptation_ref == "stage-improvement-agent@1"
    assert root.policy.strategy.feedback_timeout_seconds == 600
    assert (
        type(body.policy) is SwarmStagesPolicy
    )  # no independently writable legacy DAG
    assert body.policy.execution_ref == "openapi-bot/v1"
    assert body.policy.acceptance_ref == "trusted-evidence/v1"
    assert body.policy.overall_acceptance == "stages"
    (edge,) = composition.relations
    assert (edge.source_method, edge.target_method, edge.relation) == (
        "MultiRollout",
        "Swarm",
        CompositionRelation.NEST,
    )
    assert composition.optional_methods == ()
    assert default_composition() == composition
    assert default_composition() is not composition


@pytest.mark.asyncio
async def test_public_creation_persists_default_and_does_not_dispatch_or_call_agent(
    multi,
):
    h = multi
    rid = await create_default(h)
    assert h.profile.composition == default_composition()
    assert h.profile.stages == STAGES
    assert decode_profile(encode_profile(h.profile)) == h.profile
    snapshot = saved(h)
    assert len(snapshot.rollouts) == len(snapshot.multi.candidates) == 1
    assert snapshot.multi.policy == h.profile.composition.required_methods[0].policy
    assert snapshot.multi.phase == "CREATED" and snapshot.current_rollout_id == rid
    assert len(snapshot.multi.plans) == 1
    assert snapshot.specs[0].name == "build"
    assert snapshot.specs[0].depends_on == ()
    assert {b.method_kind for b in snapshot.bindings} == {"MultiRollout", "Swarm"}
    h.bot.send_message.assert_not_awaited()
    h.strategy.propose_adjustment.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_repeated_creations_remain_independent(multi):
    h = multi
    first = await h.service.create_product_profile("coding", STAGES)
    second = await h.service.create_product_profile("coding", STAGES)
    assert first.id != second.id and first.composition == second.composition
    one = await h.service.create_product_collet(first.id, "same request")
    two = await h.service.create_product_collet(first.id, "same request")
    assert one.id != two.id
    assert (
        h.repository.get_collet(one.id).multi.run_id
        != h.repository.get_collet(two.id).multi.run_id
    )
    h.strategy.propose_adjustment.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "port,unavailable",
    [
        ("strategy", UnavailableMultiRolloutStrategy),
        ("isolation", UnavailableMultiRolloutIsolation),
        ("notifications", UnavailableMultiRolloutNotifications),
    ],
)
async def test_profile_storage_does_not_require_runtime_host_capabilities(
    multi, port, unavailable
):
    h = multi
    setattr(h.controller, port, unavailable())
    profile = await h.service.create_product_profile("unavailable", STAGES)
    with h.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 1
    with pytest.raises(Invalid, match="not installed"):
        await h.service.create_product_collet(profile.id, "requires capabilities")
    with h.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 1
        assert session.query(ProductIdentityRow).count() == 0
    h.bot.send_message.assert_not_awaited()
    h.strategy.propose_adjustment.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_static_workflow_respects_yaml_dependencies_without_agent_planning(
    multi,
):
    h = multi
    rid = await create_default(h, h.stages)
    assert saved(h).specs[1].depends_on == ("analysis",)
    for name in ("analysis", "implementation"):
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
        h.state["value"] = "final"
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
        stage = next(s for s in saved(h).current().stages if s.name == name)
        assert stage.status is S.RUN_FINISHED
        await h.engine.report(*report(h, stage))
    assert saved(h).multi.phase == "SUCCEEDED"
    assert saved(h).collet.status is S.ACCEPTANCE_SUCCEED
    assert len(h.calls) == 2 and len(saved(h).rollouts) == 1
    h.strategy.propose_adjustment.assert_not_awaited()
    h.strategy.evaluate.assert_not_awaited()
    h.notifications.notify.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["acceptance", "runtime"])
@pytest.mark.parametrize("action", [A.RE_DO, A.OPTIMIZE, A.DO_NOTHING, A.SHUTDOWN])
async def test_failure_waits_for_human_then_agent_decides_whether_to_rollout(
    multi, failure, action
):
    h = multi
    rid = await create_default(h)
    if failure == "acceptance":
        await finish(h, rid, False)
    else:
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
        h.state["value"] = "error"
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    before = saved(h)
    history = deepcopy(before.current())
    assert before.multi.phase == "WAITING_FEEDBACK"
    assert history.status is (
        S.ACCEPTANCE_FAILED if failure == "acceptance" else S.RUN_FAILED
    )
    deadline = before.multi.waiting_until
    for _ in range(2):
        await h.worker.run_once()
    assert len(h.calls) == len(saved(h).rollouts) == 1
    h.notifications.notify.assert_awaited_once()
    h.strategy.propose_adjustment.assert_not_awaited()
    patches = (
        (StagePatch("build", "add the missing regression test"),)
        if action is A.OPTIMIZE
        else ()
    )
    h.strategy.propose_adjustment.return_value = AdjustmentProposal(
        action, "Agent reviewed human feedback", patches
    )
    result = await h.service.interactive(
        rid, "human-review", "the regression is missing"
    )
    assert result.type is action
    assert (
        await h.service.interactive(rid, "human-review", "the regression is missing")
        == result
    )
    h.strategy.propose_adjustment.assert_awaited_once()
    reference, agent_snapshot, feedback, actor = (
        h.strategy.propose_adjustment.await_args.args
    )
    assert reference == "stage-improvement-agent@1"
    assert agent_snapshot.current().rollout_id == rid
    assert feedback == "the regression is missing" and actor == h.owner
    after = saved(h)
    assert after.rollouts[0] == history
    if action in {A.RE_DO, A.OPTIMIZE}:
        assert len(after.rollouts) == 2 and after.current_rollout_id != rid
        assert after.multi.phase == "EXECUTING"
        await finish(h, after.current_rollout_id)
        assert saved(h).multi.phase == "SUCCEEDED"
    elif action is A.DO_NOTHING:
        assert len(after.rollouts) == 1 and after.multi.phase == "WAITING_FEEDBACK"
        assert after.multi.waiting_until == deadline
    else:
        assert len(after.rollouts) == 1 and after.multi.phase == "STOPPED"
        assert after.stopped


@pytest.mark.asyncio
async def test_unknown_dispatch_is_not_human_feedback_or_permission_to_retry(multi):
    h = multi
    rid = await create_default(h)
    h.bot.send_message.side_effect = TimeoutError("delivery unknown")
    with pytest.raises(TimeoutError):
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    with pytest.raises(Conflict, match="unknown"):
        await h.engine.advance(h.collet.id, h.owner, rollout_id=rid)
    snapshot = saved(h)
    assert snapshot.records[0].dispatch_state == "unknown"
    assert snapshot.multi.phase == "EXECUTING"
    assert snapshot.multi.candidates[0].slot
    assert len(snapshot.rollouts) == 1
    h.strategy.propose_adjustment.assert_not_awaited()
    h.notifications.notify.assert_not_awaited()
    h.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_default_creation_rejects_invalid_static_workflow_before_persistence(
    multi,
):
    with pytest.raises(Invalid):
        await multi.service.create_product_profile("invalid", "stages: []")
    with multi.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 0
    multi.strategy.propose_adjustment.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("keyword", [False, True])
async def test_python_creation_rejects_removed_composition_argument(multi, keyword):
    with pytest.raises(TypeError):
        if keyword:
            await multi.service.create_product_profile(
                "invalid", stages=STAGES, composition=default_composition()
            )
        else:
            await multi.service.create_product_profile(
                "invalid", default_composition(), STAGES
            )
    with multi.repository.db.orm_session() as session:
        assert session.query(ProductConfigurationRow).count() == 0
