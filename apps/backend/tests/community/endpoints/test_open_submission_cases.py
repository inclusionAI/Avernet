"""Real HTTP command-only submission; authentication owns actor/accountability."""

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ColletCreateCommand,
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    BindMethodCommand,
    MethodLifecycleServiceProtocol,
    MethodStateChangeCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)

from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/v1/collaboration/task/context_collab/open/opportunities/{opportunity_id}/applications"
_OWNER = "u_opportunity_owner"
_APPLICANT = "u_application_proxy"
_BINDING = "mb_submit_case"
_OPPORTUNITY = "opp_submit_case"
_BODY = {
    "applicant_agent_ref": "bot_uuid.owner-qualified",
    "application_id": "app_submit_case",
    "capability_evidence_refs": ["evidence://coding", "evidence://testing"],
    "proposed_artifact_refs": ["artifact://draft"],
    "requested_role_refs": ["role_binding_coder"],
    "reason": "authenticated proxy application",
}


def _seed(world):
    for user_id in (_OWNER, _APPLICANT):
        make_staff_user(world, user_id=user_id)
    actor = ActorContext(PrincipalRef("human", _OWNER), "api")
    collet = world.get(ContextServiceProtocol).create_collet(
        ColletCreateCommand(
            actor.actor,
        ),
        actor,
    )
    lifecycle = world.get(MethodLifecycleServiceProtocol)
    lifecycle.bind(
        collet.collet_id,
        BindMethodCommand(
            method_kind="Open",
            method_binding_id=_BINDING,
            parent_scope_ref="scope://open",
        ),
        actor,
    )
    lifecycle.activate(collet.collet_id, _BINDING, MethodStateChangeCommand(), actor)
    world.get(OpenMethodOpsServiceProtocol).publish_opportunity(
        OpenOpportunityCommand(
            collet_id=collet.collet_id,
            actor_context=actor,
            participation_type=ParticipationType.SINGLE_CONTRIBUTION,
            opportunity_id=_OPPORTUNITY,
        )
    )


def _assert_submission(response, world):
    result = response.json()["data"]
    store = world.get(MethodStoreProtocol)
    row = store.get_application(
        current_axes(), result["object_id"], _BINDING, result["collab_context_id"]
    )
    assert row.opportunity_ref == _OPPORTUNITY
    assert row.applicant_agent_ref == _BODY["applicant_agent_ref"]
    assert row.accountable_principal_ref == _APPLICANT  # not the task publisher
    assert row.transitioned_by == _APPLICANT
    assert row.capability_evidence_refs == tuple(_BODY["capability_evidence_refs"])
    assert row.proposed_artifact_refs == tuple(_BODY["proposed_artifact_refs"])
    assert row.requested_role_refs == tuple(_BODY["requested_role_refs"])


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="command_only_application_ok",
    input=CaseInput(
        path_params={"opportunity_id": _OPPORTUNITY},
        headers={"x-user-id": _APPLICANT},
        json_body=_BODY,
    ),
    seed=_seed,
    expect=ExpectSuccess(
        json_contains={
            "data": {
                "accepted": True,
                "state": "SUBMITTED",
                "method_binding_id": _BINDING,
            }
        }
    ),
    extra_assertions=(_assert_submission,),
)
def command_only_application_ok():
    """Resolve scope, apply G1, and persist evidence and authenticated accountability."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="command_only_application_missing_opportunity",
    input=CaseInput(
        path_params={"opportunity_id": "missing"},
        headers={"x-user-id": _APPLICANT},
        json_body=_BODY,
    ),
    seed=lambda world: make_staff_user(world, user_id=_APPLICANT),
    expect=ExpectError(status=404),
)
def command_only_application_missing_opportunity():
    """A known authenticated caller cannot submit to a nonexistent opportunity."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="command_only_application_rejects_owner_override",
    input=CaseInput(
        path_params={"opportunity_id": _OPPORTUNITY},
        headers={"x-user-id": _APPLICANT},
        json_body={**_BODY, "accountable_principal_ref": _OWNER},
    ),
    seed=_seed,
    expect=ExpectError(status=422),
)
def command_only_application_rejects_owner_override():
    """The removed command/body field cannot spoof application accountability."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="command_only_application_rejects_actor_override",
    input=CaseInput(
        path_params={"opportunity_id": _OPPORTUNITY},
        headers={"x-user-id": _APPLICANT},
        json_body={**_BODY, "actor_context": {"actor": _OWNER}},
    ),
    seed=_seed,
    expect=ExpectError(status=422),
)
def command_only_application_rejects_actor_override():
    """Trusted G1 context is not deserialized from the application JSON."""
