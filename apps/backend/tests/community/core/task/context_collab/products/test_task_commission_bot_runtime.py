"""The simulated Bot discovers opportunities and applies by declared product role."""

from dataclasses import replace
from unittest.mock import create_autospec

import pytest
from fastapi.testclient import TestClient

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    OpenOpportunity,
    OpportunityState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenApplicationCommand,
    OpenMethodOpsServiceProtocol,
    ProfileOpAcceptedResult,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    init_product_profile,
)
from tests.community.core.task.context_collab.products.task_commission_e2e_support import (
    SimulatedWorkingBot,
)


@pytest.fixture
def bot_runtime():
    open_method = create_autospec(OpenMethodOpsServiceProtocol, instance=True)
    bot = SimulatedWorkingBot(
        client=create_autospec(TestClient, instance=True),
        signed_principal="test-principal",
        profile=init_product_profile(),
        bot_id="bot_discoverer:u_bot_owner",
        owner_id="u_bot_owner",
        open_method=open_method,
    )
    return bot, open_method


@pytest.mark.parametrize(
    "opportunities",
    [
        (),
        (
            OpenOpportunity(
                opportunity_id="opp_001",
                method_binding_id="mb_001",
                scope_ref="collet_001",
                participation_type=ParticipationType.COLLET_PARTICIPANT,
                opens_at="2026-11-17T00:00:00+00:00",
                state=OpportunityState.OPEN,
            ),
        ),
    ],
    ids=["empty", "available"],
)
def test_bot_queries_injected_open_service_as_itself(bot_runtime, opportunities):
    working_bot, open_method = bot_runtime
    open_method.query_opportunities.return_value = opportunities

    assert working_bot.query_opportunities_by_skill() is opportunities

    open_method.query_opportunities.assert_called_once_with(
        actor_context=ActorContext(
            actor=PrincipalRef("bot", working_bot.bot_uuid),
            source="a2a",
            accountable_principal_ref=PrincipalRef("human", working_bot.owner_id),
        )
    )
    open_method.submit_application.assert_not_called()
    working_bot.client.post.assert_not_called()


def test_bot_propagates_opportunity_query_failure(bot_runtime):
    working_bot, open_method = bot_runtime
    open_method.query_opportunities.side_effect = (
        ContextCollabValidationError("query denied")
    )

    with pytest.raises(ContextCollabValidationError, match="query denied"):
        working_bot.query_opportunities_by_skill()


@pytest.fixture
def opportunity():
    return OpenOpportunity(
        opportunity_id="opp_001",
        method_binding_id="mb_001",
        scope_ref="collet_001",
        participation_type=ParticipationType.COLLET_PARTICIPANT,
        opens_at="2026-11-17T00:00:00+00:00",
        state=OpportunityState.OPEN,
    )


@pytest.mark.parametrize(
    ("requested_role", "binding_suffix"),
    [
        ("assignee", "selected-agent/participant_executor_ref"),
        ("owner", "responsible_user/participant_principal_ref"),
    ],
)
@pytest.mark.parametrize(
    ("scope_ref", "encoded_scope"),
    [("collet_001", "collet_001"), ("collet/a b", "collet%2Fa%20b")],
)
def test_bot_applies_with_profile_role_binding_in_opportunity_scope(
    bot_runtime, opportunity, requested_role, binding_suffix, scope_ref, encoded_scope
):
    working_bot, open_method = bot_runtime
    opportunity = replace(opportunity, scope_ref=scope_ref)
    accepted = ProfileOpAcceptedResult(
        accepted=True,
        collab_context_id=scope_ref,
        method_binding_id=opportunity.method_binding_id,
        object_kind="application",
        object_id="application_001",
        state="SUBMITTED",
    )
    open_method.submit_application.return_value = accepted

    submitted = working_bot.submit_application_by_skill(
        opportunity=opportunity,
        capability_evidence_refs=["evidence://calculator-coding"],
        requested_role=requested_role,
    )

    assert submitted is accepted
    open_method.submit_application.assert_called_once_with(
        command=OpenApplicationCommand(
            opportunity_ref=opportunity.opportunity_id,
            actor_context=working_bot.actor_context,
            applicant_agent_ref=working_bot.bot_uuid,
            capability_evidence_refs=["evidence://calculator-coding"],
            requested_role_refs=[f"{encoded_scope}/role-bindings/{binding_suffix}"],
            reason="task_commission.bot_self_application",
        )
    )
    open_method.query_opportunities.assert_not_called()
    working_bot.client.post.assert_not_called()


@pytest.mark.parametrize("requested_role", ["unknown", "selected-agent", ""])
def test_bot_rejects_roles_not_declared_in_profile(
    bot_runtime, opportunity, requested_role
):
    working_bot, open_method = bot_runtime

    with pytest.raises(ContextCollabValidationError, match="does not declare role"):
        working_bot.submit_application_by_skill(
            opportunity=opportunity,
            capability_evidence_refs=["evidence://calculator-coding"],
            requested_role=requested_role,
        )

    open_method.submit_application.assert_not_called()
    working_bot.client.post.assert_not_called()


def test_bot_propagates_application_submission_failure(bot_runtime, opportunity):
    working_bot, open_method = bot_runtime
    open_method.submit_application.side_effect = ContextCollabValidationError(
        "application denied"
    )

    with pytest.raises(ContextCollabValidationError, match="application denied"):
        working_bot.submit_application_by_skill(
            opportunity=opportunity,
            capability_evidence_refs=["evidence://calculator-coding"],
            requested_role="assignee",
        )
