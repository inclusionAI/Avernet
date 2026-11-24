"""Real HTTP review: route owns scope, authenticated user owns command context."""

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
    OpenApplicationCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
    OpenScreenCommand,
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

_BASE = "/api/v1/collaboration/task/context_collab/{collab_context_id}/open/applications/{application_id}"
_OWNER = "u_open_reviewer"
_BINDING = "mb_review_case"
_APP = "app_review_case"
_REASON = "policy://review"
# Seeds replace the generated Collet ID before the framework formats the URL.
_PARAMS = {"application_id": _APP}
_FOREIGN_PARAMS = {"application_id": "foreign_application"}


def _seed(world):
    make_staff_user(world, user_id=_OWNER)
    context = ActorContext(PrincipalRef("human", _OWNER), "api")
    collet = world.get(ContextServiceProtocol).create_collet(
        ColletCreateCommand(
            initiator_ref=context.actor,
        ),
        context,
    )
    for params in (_PARAMS, _FOREIGN_PARAMS):
        params["collab_context_id"] = collet.collet_id
    lifecycle = world.get(MethodLifecycleServiceProtocol)
    lifecycle.bind(
        collet.collet_id,
        BindMethodCommand(
            method_kind="Open",
            method_binding_id=_BINDING,
            parent_scope_ref="scope://nested",
        ),
        context,
    )
    lifecycle.activate(collet.collet_id, _BINDING, MethodStateChangeCommand(), context)
    service = world.get(OpenMethodOpsServiceProtocol)
    opportunity = service.publish_opportunity(
        OpenOpportunityCommand(
            collet_id=collet.collet_id,
            participation_type=ParticipationType.COLLET_PARTICIPANT,
        )
    )
    service.submit_application_by_skill(
        OpenApplicationCommand(
            opportunity_ref=opportunity.object_id,
            actor_context=context,
            applicant_agent_ref="bot_uuid.review",
            application_id=_APP,
        )
    )


def _seed_screened(world):
    _seed(world)
    world.get(OpenMethodOpsServiceProtocol).screen(
        OpenScreenCommand(
            application_ref=_APP,
            context=ActorContext(PrincipalRef("human", _OWNER), "api"),
            collect_id=_PARAMS["collab_context_id"],
        )
    )


def _assert_review(response, world):
    data = response.json()["data"]
    store = world.get(MethodStoreProtocol)
    axes, collet_id = current_axes(), data["collab_context_id"]
    assert data["method_binding_id"] == _BINDING
    row = store.get_application(axes, _APP, _BINDING, collet_id)
    assert row.transitioned_by == _OWNER
    assert row.transition_reason == _REASON
    if data["state"] == "ADMITTED":
        [decision] = store.list_decisions(axes, _BINDING, collet_id)
        [participant] = store.list_participant_bindings(axes, _BINDING, collet_id)
        assert decision.application_ref == _APP
        assert decision.admitted_scope_ref == participant.scope_ref == collet_id


@endpoint_test(
    method="POST",
    path=_BASE + "/screen",
    scenario="command_only_review_screen_ok",
    input=CaseInput(
        path_params=_PARAMS,
        headers={"x-user-id": _OWNER},
        json_body={"reason": _REASON},
    ),
    seed=_seed,
    expect=ExpectSuccess(
        json_contains={"data": {"accepted": True, "state": "SCREENING"}}
    ),
    extra_assertions=(_assert_review,),
)
def command_only_review_screen_ok():
    """Resolve application binding and record the authenticated reviewer."""


@endpoint_test(
    method="POST",
    path=_BASE + "/admit",
    scenario="command_only_review_admit_ok",
    input=CaseInput(
        path_params=_PARAMS,
        headers={"x-user-id": _OWNER},
        json_body={"decision_id": "dec_review_case", "reason": _REASON},
    ),
    seed=_seed_screened,
    expect=ExpectSuccess(
        json_contains={"data": {"accepted": True, "state": "ADMITTED"}}
    ),
    extra_assertions=(_assert_review,),
)
def command_only_review_admit_ok():
    """Persist the decision and immutable participation with Collet-derived scope."""


@endpoint_test(
    method="POST",
    path=_BASE + "/screen",
    scenario="command_only_review_screen_missing_app",
    input=CaseInput(
        path_params=_FOREIGN_PARAMS,
        headers={"x-user-id": _OWNER},
        json_body={},
    ),
    seed=_seed,
    expect=ExpectError(status=404),
)
def command_only_review_screen_missing_app():
    """An application outside the route's Collet cannot be screened."""


@endpoint_test(
    method="POST",
    path=_BASE + "/admit",
    scenario="command_only_review_admit_missing_app",
    input=CaseInput(
        path_params=_FOREIGN_PARAMS,
        headers={"x-user-id": _OWNER},
        json_body={},
    ),
    seed=_seed_screened,
    expect=ExpectError(status=404),
)
def command_only_review_admit_missing_app():
    """An application outside the route's Collet cannot be admitted."""


@endpoint_test(
    method="POST",
    path=_BASE + "/screen",
    scenario="command_only_review_screen_context_override",
    input=CaseInput(
        path_params=_PARAMS,
        headers={"x-user-id": _OWNER},
        json_body={"context": {"actor": "another_reviewer"}},
    ),
    seed=_seed,
    expect=ExpectError(status=422),
)
def command_only_review_screen_context_override():
    """Review context can only come from verified authentication."""


@endpoint_test(
    method="POST",
    path=_BASE + "/admit",
    scenario="command_only_review_admit_scope_override",
    input=CaseInput(
        path_params=_PARAMS,
        headers={"x-user-id": _OWNER},
        json_body={"admitted_scope_ref": "foreign_collet"},
    ),
    seed=_seed_screened,
    expect=ExpectError(status=422),
)
def command_only_review_admit_scope_override():
    """Removed admission scope is rejected rather than silently ignored."""


@endpoint_test(
    method="POST",
    path=_BASE + "/admit",
    scenario="command_only_review_admit_context_override",
    input=CaseInput(
        path_params=_PARAMS,
        headers={"x-user-id": _OWNER},
        json_body={"context": {"actor": "another_reviewer"}},
    ),
    seed=_seed_screened,
    expect=ExpectError(status=422),
)
def command_only_review_admit_context_override():
    """Admission must use the authenticated reviewer, not JSON context."""
