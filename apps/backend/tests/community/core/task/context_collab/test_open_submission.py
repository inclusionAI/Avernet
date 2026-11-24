"""Command-only Open submission: scoped lookup, trusted identity and evidence."""

import inspect
from dataclasses import fields, replace
from unittest.mock import Mock

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.service import (
    OpenMethodService,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from tests.community.core.task.context_collab.open_profile_support import (
    AXES,
    COLLET_ID,
    active_open_binding,
    application_command,
    make_actor,
    make_open_service,
    opportunity_command,
)


@pytest.fixture
def published():
    store, repo, lifecycle, service = make_open_service()
    binding = active_open_binding(lifecycle)
    service.publish_opportunity(opportunity_command())
    return store, repo, binding, service


def _row(store, binding, application_id="app_1"):
    return store.get_application(AXES, application_id, binding, COLLET_ID)


def test_command_only_contract():
    for cls in (OpenMethodOpsServiceProtocol, OpenMethodService):
        assert list(inspect.signature(cls.submit_application).parameters) == [
            "self",
            "command",
        ]
    names = [field.name for field in fields(OpenApplicationCommand)]
    assert names[:3] == ["opportunity_ref", "actor_context", "applicant_agent_ref"]
    assert "capability_evidence_refs" in names
    assert not {
        "accountable_principal_ref",
        "collab_context_id",
        "method_binding_id",
    } & set(names)
    with pytest.raises(TypeError, match="accountable_principal_ref"):
        application_command(accountable_principal_ref="forged_owner")


def test_resolves_exact_opportunity_with_multiple_active_bindings_and_copies_evidence(
    published,
):
    store, _, binding, service = published
    source = store.get_binding(AXES, binding, COLLET_ID)
    other = replace(source, method_binding_id="mb_other")
    store.upsert_binding(AXES, other)
    opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.insert_opportunity(
        AXES,
        replace(opportunity, opportunity_id="opp_other", method_binding_id="mb_other"),
        COLLET_ID,
    )
    command = application_command(
        capability_evidence_refs=["evidence://a", "evidence://b"]
    )
    result = service.submit_application(command=command)
    row = _row(store, binding)
    assert (result.collab_context_id, result.method_binding_id) == (COLLET_ID, binding)
    assert row.capability_evidence_refs == ("evidence://a", "evidence://b")
    assert row.proposed_artifact_refs == tuple(command.proposed_artifact_refs)
    assert row.requested_role_refs == tuple(command.requested_role_refs)
    assert row.accountable_principal_ref == command.actor_context.actor.principal_id
    assert row.transitioned_by == command.actor_context.actor.principal_id
    command.capability_evidence_refs.append("evidence://later")
    assert _row(store, binding).capability_evidence_refs == (
        "evidence://a",
        "evidence://b",
    )
    assert store.list_applications(AXES, "mb_other", COLLET_ID) == []


def test_bot_uuid_and_signed_owner_differ_from_publisher_and_preserve_withdraw(
    published,
):
    store, _, binding, service = published
    owner = PrincipalRef("human", "other_bot_owner")
    actor = ActorContext(PrincipalRef("bot", "bot_uuid.owner-qualified"), "a2a", owner)
    service.submit_application(
        application_command(
            actor_context=actor,
            applicant_agent_ref=actor.actor.principal_id,
        )
    )
    row = _row(store, binding)
    assert row.applicant_agent_ref == actor.actor.principal_id
    assert row.accountable_principal_ref == owner.principal_id
    assert row.transitioned_by == actor.actor.principal_id
    with pytest.raises(ContextCollabValidationError):
        service.withdraw(
            COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), make_actor()
        )
    result = service.withdraw(
        COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), ActorContext(owner, "api")
    )
    assert result.state == "WITHDRAWN"


@pytest.mark.parametrize(
    "case",
    ["missing_owner", "empty_owner", "wrong_applicant", "human_override", "anonymous"],
)
def test_missing_or_inconsistent_identity_is_rejected_without_write(published, case):
    store, _, binding, service = published
    actor = ActorContext(
        PrincipalRef("bot", "bot_uuid"), "a2a", PrincipalRef("human", "owner")
    )
    applicant = "bot_uuid"
    if case == "missing_owner":
        actor = replace(actor, accountable_principal_ref=None)
    elif case == "empty_owner":
        actor = replace(actor, accountable_principal_ref=PrincipalRef("human", ""))
    elif case == "wrong_applicant":
        applicant = "another_bot"
    elif case == "human_override":
        actor = replace(
            make_actor(), accountable_principal_ref=PrincipalRef("human", "other")
        )
    else:
        actor = make_actor("")
    with pytest.raises(ContextCollabValidationError):
        service.submit_application(
            application_command(actor_context=actor, applicant_agent_ref=applicant)
        )
    assert store.list_applications(AXES, binding, COLLET_ID) == []


@pytest.mark.parametrize("ref", ["", " ", None, 1])
def test_invalid_opportunity_ref_rejected(published, ref):
    store, _, binding, service = published
    with pytest.raises(ContextCollabValidationError, match="opportunity_ref"):
        service.submit_application(application_command(opportunity_ref=ref))
    assert _row(store, binding) is None


@pytest.mark.parametrize("same_collet", [True, False])
@pytest.mark.parametrize("state", [BindingState.ACTIVE, BindingState.BOUND])
def test_ambiguous_ref_never_picks_first_or_active_candidate(
    published, same_collet, state
):
    store, _, binding, service = published
    collet = COLLET_ID if same_collet else "other_collet"
    other = replace(
        store.get_binding(AXES, binding, COLLET_ID),
        method_binding_id="mb_other",
        collab_context_id=collet,
        state=state,
    )
    store.upsert_binding(AXES, other)
    original = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.insert_opportunity(
        AXES, replace(original, method_binding_id="mb_other", scope_ref=collet), collet
    )
    with pytest.raises(ContextCollabValidationError, match="ambiguous opportunity_ref"):
        service.submit_application(application_command())
    assert _row(store, binding) is None
    assert store.list_applications(AXES, "mb_other", collet) == []


@pytest.mark.parametrize("axis", ["tenant", "env"])
def test_lookup_is_isolated_by_current_axes(published, axis):
    store, _, binding, service = published
    other = (
        StoreAxes("other_tenant", AXES.env)
        if axis == "tenant"
        else StoreAxes(AXES.avernet_tenant, "other_env")
    )
    store.upsert_binding(other, store.get_binding(AXES, binding, COLLET_ID))
    opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
    store.insert_opportunity(other, opportunity, COLLET_ID)
    store.insert_opportunity(
        other, replace(opportunity, opportunity_id="foreign_only"), COLLET_ID
    )
    with pytest.raises(ContextCollabNotFoundError):
        service.submit_application(application_command(opportunity_ref="foreign_only"))
    service.submit_application(
        application_command()
    )  # foreign duplicate is not ambiguous
    assert _row(store, binding) is not None
    assert store.list_applications(other, binding, COLLET_ID) == []


@pytest.mark.parametrize("invalid", ["inactive", "non_open"])
def test_resolved_binding_still_runs_structural_guards(published, invalid):
    store, _, binding, service = published
    original = store.get_binding(AXES, binding, COLLET_ID)
    changed = (
        replace(original, state=BindingState.BOUND)
        if invalid == "inactive"
        else replace(original, method_names=("Group",))
    )
    store.replace_binding(AXES, changed)
    with pytest.raises(ContextCollabValidationError):
        service.submit_application(application_command())
    assert _row(store, binding) is None


@pytest.mark.parametrize(
    "operation", ["list_bindings_in_axes", "get_opportunity", "insert_application"]
)
def test_storage_failures_propagate(published, operation):
    store, repo, binding, _ = published
    failing = Mock(spec=MethodStoreProtocol, wraps=store)
    getattr(failing, operation).side_effect = RuntimeError("storage unavailable")
    service = OpenMethodService(failing, StructuralMethodOpsAuthorizer(), repo)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        service.submit_application(application_command())
    getattr(failing, operation).assert_called_once()
    assert _row(store, binding) is None


def test_idempotency_keeps_owner_and_evidence_as_request_shape(published):
    store, _, binding, service = published
    command = application_command()
    first = service.submit_application(command)
    row = _row(store, binding)
    assert service.submit_application(command).object_id == first.object_id
    assert _row(store, binding) == row
    for changed in (
        replace(command, actor_context=make_actor("other_owner")),
        replace(command, capability_evidence_refs=["evidence://different"]),
    ):
        with pytest.raises(ContextCollabValidationError):
            service.submit_application(changed)
        assert _row(store, binding) == row


def test_submission_uses_own_g1_policy_not_discovery_authorization(published):
    store, repo, binding, _ = published
    authorizer = Mock(wraps=StructuralMethodOpsAuthorizer())
    authorizer.check_open_discovery.side_effect = AssertionError("not a discovery call")
    authorizer.check_open_op.side_effect = ContextCollabValidationError(
        "submission denied"
    )
    service = OpenMethodService(store, authorizer, repo)
    with pytest.raises(ContextCollabValidationError, match="submission denied"):
        service.submit_application(application_command())
    assert authorizer.check_open_op.call_args.args[0] == "submit_application"
    authorizer.check_open_discovery.assert_not_called()
    assert _row(store, binding) is None
