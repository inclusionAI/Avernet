"""The local reviewer uses submitted capability declarations and Profile needs."""

from dataclasses import replace

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationApplication,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    OpenAdmissionPolicy,
)
from tests.community.core.task.context_collab.products.open_admission_support import (
    LocalCapabilityAdmissionService,
)

CODING = "capability://ai-coding/coding"
VERIFICATION = "capability://ai-coding/verification"
COPYWRITING = "capability://copywriting"


@pytest.fixture
def policy():
    return OpenAdmissionPolicy(
        applicant_role="submitter",
        accountable_role="owner",
        participation_type=ParticipationType.COLLET_PARTICIPANT,
        capacity=7,
        decision_rule="capability_evidence",
        required_capabilities=(CODING, VERIFICATION),
    )


@pytest.fixture
def application():
    return ParticipationApplication(
        application_id="application",
        opportunity_ref="opportunity",
        applicant_agent_ref="arbitrary_new_bot",
        accountable_principal_ref="bot_owner",
        state=ApplicationState.SCREENING,
        submitted_at="2026-11-19T00:00:00+00:00",
        capability_evidence_refs=(CODING,),
    )


@pytest.mark.parametrize(
    "declarations, accepted",
    [
        ((CODING,), True),  # One Bot need not implement all requested capabilities.
        ((VERIFICATION,), True),
        ((CODING, VERIFICATION), True),
        ((CODING, COPYWRITING), True),  # Extra abilities do not disqualify a match.
        ((COPYWRITING,), False),
        ((), False),
        (("capability://ai-coding/coding-extra",), False),  # No prefix matching.
    ],
)
def test_reviewer_matches_any_exact_required_capability(
    policy, application, declarations, accepted
):
    application = replace(application, capability_evidence_refs=declarations)
    reviewer = LocalCapabilityAdmissionService()
    assert reviewer.should_admit(policy, application) is accepted
    assert reviewer.calls == [(policy, application)]
    assert application.state is ApplicationState.SCREENING  # Review grants nothing.


def test_reviewer_reads_configured_needs_instead_of_a_hardcoded_coding_list(
    policy, application
):
    reviewer = LocalCapabilityAdmissionService()
    assert reviewer.should_admit(policy, application)
    assert not reviewer.should_admit(
        replace(policy, required_capabilities=(VERIFICATION,)), application
    )
    assert not reviewer.should_admit(
        replace(policy, required_capabilities=()), application
    )


def test_reviewer_is_independent_of_bot_identity_and_preassigned_role(
    policy, application
):
    reviewer = LocalCapabilityAdmissionService()
    assert reviewer.should_admit(policy, application)
    assert reviewer.should_admit(
        policy,
        replace(
            application,
            applicant_agent_ref="another_bot_with_same_capability",
            requested_role_refs=("another_role",),
        ),
    )
    assert not reviewer.should_admit(
        policy,
        replace(
            application,
            applicant_agent_ref="coding_coding",
            capability_evidence_refs=(),
        ),
    )


def test_capability_reviewer_rejects_an_unsupported_rule(policy, application):
    reviewer = LocalCapabilityAdmissionService()
    with pytest.raises(ContextCollabValidationError, match="capability_evidence rule"):
        reviewer.should_admit(
            replace(policy, decision_rule="product_policy"), application
        )
    assert reviewer.calls == []
