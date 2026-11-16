"""Registered endpoint cases for binding-free Open publication with real services."""

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ColletCreateCommand,
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    BindMethodCommand,
    MethodLifecycleServiceProtocol,
    MethodStateChangeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_PATH = "/api/v1/collaboration/task/context_collab/{collab_context_id}/open/opportunities"
_OWNER = "u_open_publisher"
_BINDING = "mb_open_publish_case"
_BODY = {
    "scope_ref": "scope://contribution",
    "participation_type": "SINGLE_CONTRIBUTION",
    "opens_at": "2026-01-01T00:00:00+00:00",
    "opportunity_id": "opp_publish_case",
    "reason": "authenticated publication",
}
# Collet IDs are service-generated. The seed fills this case's path parameters
# afresh before the runner formats its URL; no database IDs are fabricated.
_HAPPY_PARAMS: dict[str, str] = {}


def _seed_publication(world):
    make_staff_user(world, user_id=_OWNER)
    actor = ActorContext(PrincipalRef("human", _OWNER), "api")
    collet = world.get(ContextServiceProtocol).create_collet(
        ColletCreateCommand(
            to_space=SpaceRef("space_publication", "prod"),
            principal_ref=actor.actor,
            objective="Publish a contribution opportunity",
        ),
        actor,
    )
    _HAPPY_PARAMS["collab_context_id"] = collet.collet_id
    lifecycle = world.get(MethodLifecycleServiceProtocol)
    lifecycle.bind(
        collet.collet_id,
        BindMethodCommand(
            method_kind="Open", method_binding_id=_BINDING,
            parent_scope_ref="scope://nested",
        ),
        actor,
    )
    lifecycle.activate(collet.collet_id, _BINDING, MethodStateChangeCommand(), actor)


def _assert_publication(response, world):
    result = response.json()["data"]
    assert result["collab_context_id"] == _HAPPY_PARAMS["collab_context_id"]
    row = world.get(MethodStoreProtocol).get_opportunity(
        current_axes(), result["object_id"], _BINDING, result["collab_context_id"]
    )
    assert row.transitioned_by == _OWNER
    assert row.transition_reason == _BODY["reason"]


@endpoint_test(
    method="POST", path=_PATH, scenario="binding_free_publish_ok",
    input=CaseInput(
        path_params=_HAPPY_PARAMS, headers={"x-user-id": _OWNER}, json_body=_BODY,
    ),
    seed=_seed_publication,
    expect=ExpectSuccess(json_contains={
        "data": {"accepted": True, "state": "OPEN", "method_binding_id": _BINDING},
    }),
    extra_assertions=(_assert_publication,),
)
def binding_free_publish_ok():
    """Real L4 creation + L5 resolution, authorization, transition and audit."""


@endpoint_test(
    method="POST", path=_PATH, scenario="binding_free_publish_missing_open_binding",
    input=CaseInput(
        path_params={"collab_context_id": "missing_collet"},
        headers={"x-user-id": _OWNER}, json_body=_BODY,
    ),
    seed=lambda world: make_staff_user(world, user_id=_OWNER),
    expect=ExpectError(status=404),
)
def binding_free_publish_missing_open_binding():
    """The service cannot resolve an Open binding in a nonexistent Collet."""
