from dataclasses import replace
import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation as R,
    validate_composition,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_policy import (
    MultiRolloutLimits,
    ParallelBestOf,
    AdaptiveUntilAccepted,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.configuration import (
    encode_composition,
    decode_composition,
    validate_configuration,
)
from .conftest import policy, composition


@pytest.mark.parametrize(
    "cls,kwargs",
    [
        (
            ParallelBestOf,
            dict(samples=True, max_concurrency=1, selection_ref="highest-score@1"),
        ),
        (
            ParallelBestOf,
            dict(samples=33, max_concurrency=1, selection_ref="highest-score@1"),
        ),
        (
            ParallelBestOf,
            dict(samples=4, max_concurrency=5, selection_ref="highest-score@1"),
        ),
        (
            ParallelBestOf,
            dict(samples=9, max_concurrency=9, selection_ref="highest-score@1"),
        ),
        (
            AdaptiveUntilAccepted,
            dict(feedback_mode="interactive", adaptation_ref="agent@1"),
        ),
        (
            AdaptiveUntilAccepted,
            dict(
                feedback_mode="autonomous",
                adaptation_ref="agent@1",
                feedback_timeout_seconds=30,
            ),
        ),
        (
            AdaptiveUntilAccepted,
            dict(
                feedback_mode="interactive",
                adaptation_ref="agent@1",
                feedback_timeout_seconds=True,
            ),
        ),
    ],
)
def test_policy_rejects_invalid_values(cls, kwargs):
    with pytest.raises(Invalid):
        cls(**kwargs)


@pytest.mark.parametrize(
    "name,high",
    [
        ("max_rollouts", 32),
        ("max_elapsed_seconds", 86400),
        ("max_stage_dispatches", 3200),
        ("max_strategy_calls", 64),
        ("max_no_progress", 8),
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, "1", 1.2, None, 100000])
def test_all_budget_bounds(name, high, value):
    data = dict(
        max_rollouts=1,
        max_elapsed_seconds=1,
        max_stage_dispatches=1,
        max_strategy_calls=1,
        max_no_progress=1,
    )
    data[name] = value
    with pytest.raises(Invalid):
        MultiRolloutLimits(**data)


def test_exact_profile_and_matrix_do_not_expand_existing_relations():
    assert get_profile("MultiRollout", "1.0").participation_model == "body"
    for method, version in (
        ("MultiRollout", "0.2"),
        ("MultiRollout", "1.1"),
        ("Swarm", "1.0"),
    ):
        with pytest.raises(Invalid):
            get_profile(method, version)
    for child in ("Single", "Group", "Swarm"):
        validate_composition("MultiRollout", R.NEST, child)
    for source, relation, target in (
        ("MultiRollout", R.NEST, "MultiRollout"),
        ("MultiRollout", R.NEST, "Open"),
        ("Swarm", R.NEST, "MultiRollout"),
        ("MultiRollout", R.PARALLELIZE, "Swarm"),
    ):
        with pytest.raises(Invalid):
            validate_composition(source, relation, target)


@pytest.mark.parametrize(
    "strategy",
    [
        ParallelBestOf(4, 2, "highest-score@1"),
        AdaptiveUntilAccepted("interactive", "stage-agent@1", 600),
        AdaptiveUntilAccepted("autonomous", "stage-agent@1"),
    ],
)
def test_closed_codec_roundtrip(strategy):
    original = composition(policy(strategy))
    encoded = encode_composition(original)
    assert decode_composition(encoded) == original
    raw = encoded["required_methods"][0]["policy"]
    assert "body_method" not in raw and "method" not in raw
    if (
        isinstance(strategy, AdaptiveUntilAccepted)
        and strategy.feedback_mode == "autonomous"
    ):
        assert "feedback_timeout_seconds" not in raw["strategy"]
        raw["strategy"]["feedback_timeout_seconds"] = None
        with pytest.raises(Invalid):
            decode_composition(encoded)
    raw["body_method"] = "Swarm"
    with pytest.raises(Invalid):
        decode_composition(encoded)


@pytest.mark.asyncio
async def test_explicit_relation_order_capabilities_and_opt_in(multi):
    h = await multi.create(reverse=True)
    snapshot = h.repository.get_collet(h.collet.id)
    assert snapshot.multi.body_binding.endswith("Swarm")
    assert len(snapshot.rollouts) == 4
    no_relation = replace(composition(policy()), relations=())
    with pytest.raises(Invalid):
        validate_configuration(no_relation, h.profile.stages, h.execution)
    with pytest.raises(Invalid, match="must equal"):
        policy(max_rollouts=3)
    with pytest.raises(Invalid, match="not configured"):
        from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
            ProductProfileService,
        )

        service = ProductProfileService(
            h.repository,
            h.service._publisher,
            h.authorization,
            h.execution,
            h.interaction,
            h.service._messages,
        )
        await service.create_product_profile(
            "denied", composition(policy()), h.profile.stages
        )


@pytest.mark.asyncio
async def test_isolation_effects_and_autonomous_delegation_fail_closed(multi):
    h = multi
    h.isolation.unsafe = True
    with pytest.raises(Invalid, match="irreversible"):
        await h.create()
    h.isolation.unsafe = False
    with pytest.raises(Exception, match="delegation"):
        await h.create(policy(AdaptiveUntilAccepted("autonomous", "stage-agent@1")))
