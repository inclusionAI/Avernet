"""Open policy values are L5 contracts, independent of L6 scenario-role types."""

from dataclasses import FrozenInstanceError, dataclass, replace
from typing import get_type_hints

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    MethodPolicy,
    OpenMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    AdmissionRole,
    BotSelfApplicationAdmission,
    OpenAdmissionPolicy,
    OpenPublicationProfile,
)


@dataclass(frozen=True)
class Role:
    name: str
    participant_source: str
    required: bool = True


@pytest.fixture
def admission():
    return BotSelfApplicationAdmission(
        applicant_role="worker",
        accountable_role="publisher",
        participation_type=ParticipationType.COLLET_PARTICIPANT,
        capacity=1,
    )


@pytest.fixture
def roles():
    return (Role("worker", "executor_ref"), Role("publisher", "principal_ref"))


def test_l5_contracts_accept_structural_roles_without_l6_definitions(admission, roles):
    admission.validate_roles(roles)
    assert get_type_hints(OpenAdmissionPolicy.validate_roles)["roles"] == tuple[
        AdmissionRole, ...
    ]
    assert get_type_hints(BotSelfApplicationAdmission.validate_roles)["roles"] == tuple[
        AdmissionRole, ...
    ]
    publication = OpenPublicationProfile(admission)
    policy = OpenMethodPolicy(publication)
    assert MethodPolicy is OpenMethodPolicy
    assert policy.publication.admission is admission
    assert policy.method == "Open"
    for value, field, replacement in (
        (admission, "capacity", 2),
        (publication, "partial_failure", "ignore"),
        (policy, "method", "Single"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)


@pytest.mark.parametrize(
    "overrides",
    [
        {"strategy": "publisher_mediated"},
        {"capacity": 2},
        {"participation_type": ParticipationType.SINGLE_CONTRIBUTION},
    ],
)
def test_self_application_keeps_its_bounded_admission_rules(admission, overrides):
    with pytest.raises(ContextCollabValidationError, match="self-application policy"):
        replace(admission, **overrides)


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"applicant_role": " "}, "requires scenario roles"),
        ({"accountable_role": ""}, "requires scenario roles"),
        ({"capacity": 0}, "capacity must be positive"),
        ({"capacity": True}, "capacity must be positive"),
        ({"participation_type": "COLLET_PARTICIPANT"}, "invalid Open participation"),
    ],
)
def test_shared_admission_validation_is_preserved(admission, overrides, message):
    with pytest.raises(ContextCollabValidationError, match=message):
        replace(admission, **overrides)


@pytest.mark.parametrize(
    "roles, message",
    [
        ((Role("worker", "executor_ref"),), "required typed subject"),
        (
            (Role("worker", "executor_ref", False), Role("publisher", "principal_ref")),
            "required typed subject",
        ),
        (
            (Role("worker", "principal_ref"), Role("publisher", "executor_ref")),
            "designated executor and owner",
        ),
    ],
)
def test_self_application_checks_required_roles_and_sources(admission, roles, message):
    with pytest.raises(ContextCollabValidationError, match=message):
        admission.validate_roles(roles)


def test_publication_still_requires_admitted_delivery_and_propagates_failures(admission):
    publication = OpenPublicationProfile(admission)
    publication.require_dispatch_ready(ApplicationState.ADMITTED.value)
    with pytest.raises(ContextCollabValidationError, match="requires an ADMITTED"):
        publication.require_dispatch_ready(ApplicationState.SCREENING.value)
    for overrides in (
        {"dispatch_after": ApplicationState.SUBMITTED},
        {"partial_failure": "ignore"},
    ):
        with pytest.raises(ContextCollabValidationError, match="failure/delivery policy"):
            replace(publication, **overrides)


def test_old_policy_imports_are_identity_reexports_of_l5():
    from agentclaw.community.core.task.context_collab.l6_products.method_policies import (
        MethodPolicy as LegacyMethodPolicy,
        OpenMethodPolicy as LegacyOpenMethodPolicy,
    )
    from agentclaw.community.core.task.context_collab.l6_products.publication import (
        OpenAdmissionPolicy as LegacyOpenAdmissionPolicy,
        OpenPublicationProfile as LegacyOpenPublicationProfile,
    )
    from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
        BotSelfApplicationAdmission as LegacyBotSelfApplicationAdmission,
    )

    assert LegacyMethodPolicy is MethodPolicy
    for legacy, canonical in (
        (LegacyOpenMethodPolicy, OpenMethodPolicy),
        (LegacyOpenAdmissionPolicy, OpenAdmissionPolicy),
        (LegacyOpenPublicationProfile, OpenPublicationProfile),
        (LegacyBotSelfApplicationAdmission, BotSelfApplicationAdmission),
    ):
        assert legacy is canonical
        assert canonical.__module__.startswith(
            "agentclaw.community.core.task.context_collab.l5_methods."
        )
