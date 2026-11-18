"""Fixed product role assignments require scoped, explicit L4 participation."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.matching import (
    stage_role_candidates,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import Stage
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    role_binding_refs,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    ScenarioRole,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
    ParticipantScopeKind,
)
from agentclaw.community.core.task.context_collab.l6_products.participation import (
    product_participation,
)
from agentclaw.community.core.task.context_collab.l6_products.validation import (
    validate_product_profile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from tests.community.core.task.context_collab.swarm_profile_support import stage_dict

OWNER = PrincipalRef("human", "owner")
CODER, REVIEWER = AgentRef("bot", "coder/one"), AgentRef("bot", "reviewer")


def _profile():
    return replace(
        TASK_COMMISSION_PROFILE,
        participant_scope=ParticipantScope(
            ParticipantScopeKind.ALLOWLIST, (CODER, REVIEWER)
        ),
        scenario_roles=TASK_COMMISSION_PROFILE.scenario_roles
        + (
            ScenarioRole("coder", "coder", "agent_ref", True, ("implement",), CODER),
            ScenarioRole(
                "reviewer", "reviewer", "agent_ref", True, ("review",), REVIEWER
            ),
        ),
    )


def test_fixed_roles_have_exact_participant_refs_including_executor_alias():
    profile = _profile()
    validate_product_profile(profile)
    facts = product_participation(profile, OWNER, (CODER, REVIEWER), REVIEWER)
    assert len(facts.participants) == 3
    reviewer = next(p for p in facts.participants if p.agent_ref == REVIEWER)
    assert reviewer.participant_id == "participant_executor_ref"
    assert set(role_binding_refs("collet", profile=profile, executor_ref=REVIEWER)) == {
        role_binding_ref("collet", b) for b in facts.role_bindings
    }
    coder_ref = next(
        role_binding_ref("collet", b) for b in facts.role_bindings if b.role == "coder"
    )
    stage = Stage.from_dict(stage_dict(requirement_refs=[coder_ref]))
    assert [p.agent_ref for p in stage_role_candidates("collet", stage, facts)] == [
        CODER
    ]
    both = replace(
        stage,
        requirement_refs=tuple(
            role_binding_ref("collet", b)
            for b in facts.role_bindings
            if b.role in {"coder", "reviewer"}
        ),
    )
    assert stage_role_candidates("collet", both, facts) == ()


def test_missing_or_outside_agent_cannot_become_a_participant_implicitly():
    profile = _profile()
    with pytest.raises(ContextCollabValidationError, match="participant_refs"):
        product_participation(profile, OWNER, (REVIEWER,), REVIEWER)
    with pytest.raises(ContextCollabValidationError, match="participant scope"):
        product_participation(
            profile, OWNER, (CODER, REVIEWER, AgentRef("bot", "outsider")), REVIEWER
        )


@pytest.mark.parametrize("agent", [None, "coder", AgentRef("bot", "outsider")])
def test_invalid_fixed_agent_declaration_is_rejected(agent):
    profile = _profile()
    invalid = replace(profile.scenario_roles[-1], agent_ref=agent)
    with pytest.raises(
        ContextCollabValidationError, match="typed AgentRef|participant scope"
    ):
        validate_product_profile(
            replace(profile, scenario_roles=profile.scenario_roles[:-1] + (invalid,))
        )


def test_literal_subject_cannot_be_attached_to_legacy_identity_source():
    profile = _profile()
    invalid = replace(profile.scenario_roles[0], agent_ref=CODER)
    with pytest.raises(ContextCollabValidationError, match="participant source"):
        validate_product_profile(
            replace(profile, scenario_roles=(invalid,) + profile.scenario_roles[1:])
        )


@pytest.mark.parametrize(
    "reference",
    [
        "collet/role-bindings/ghost/participant",
        "foreign/role-bindings/coder/participant",
    ],
)
def test_matching_rejects_unknown_or_foreign_role_reference(reference):
    facts = product_participation(_profile(), OWNER, (CODER, REVIEWER), REVIEWER)
    stage = Stage.from_dict(stage_dict(requirement_refs=[reference]))
    with pytest.raises(ContextCollabValidationError, match="unknown L4 RoleBinding"):
        stage_role_candidates("collet", stage, facts)


def test_literal_roles_are_materialized_without_designated_executor():
    profile = _profile()
    profile = replace(
        profile,
        scenario_roles=tuple(
            replace(role, required=False)
            if role.participant_source == "executor_ref"
            else role
            for role in profile.scenario_roles
        ),
    )
    facts = product_participation(profile, OWNER, (CODER, REVIEWER), None)
    assert {b.role for b in facts.role_bindings} >= {"coder", "reviewer"}
    assert all(
        b.participant_id != "participant_executor_ref" for b in facts.role_bindings
    )
    assert set(role_binding_refs("collet", profile=profile)) == {
        role_binding_ref("collet", binding) for binding in facts.role_bindings
    }
