"""Real Open opportunity/application lifecycle HTTP cases for the coverage gate."""

from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
    OpenOpportunityOpCommand,
    OpenScreenCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from tests.community.factories.context_collab_methods import (
    BASE,
    BINDING,
    HEADERS,
    OWNER,
    seed_method,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OPPORTUNITY = "opportunity_http"
_APPLICATION = "application_http"


def _register(resource, operation, state, happy):
    params = {
        "binding_id": BINDING,
        "opportunity_id": _OPPORTUNITY,
        "application_id": _APPLICATION,
    }
    before = {}

    def seed(world):
        collet_id = seed_method(world, "Open")
        params["collab_context_id"] = collet_id
        service = world.get(OpenMethodOpsServiceProtocol)
        service.publish_opportunity(
            OpenOpportunityCommand(
                collet_id,
                ParticipationType.COLLET_PARTICIPANT,
                opportunity_id=_OPPORTUNITY,
            ),
            context=OWNER,
        )
        if resource == "opportunities" and operation == "reopen":
            service.pause_opportunity(
                collet_id, BINDING, _OPPORTUNITY, OpenOpportunityOpCommand(), OWNER
            )
        if resource == "applications":
            service.submit_application(
                OpenApplicationCommand(
                    _OPPORTUNITY,
                    OWNER,
                    "bot_http_worker",
                    application_id=_APPLICATION,
                )
            )
            if operation == "revoke":
                service.screen(OpenScreenCommand(_APPLICATION, OWNER, collet_id))
                service.admit(OpenAdmitCommand(_APPLICATION, OWNER, collet_id))
        store, axes = world.get(MethodStoreProtocol), current_axes()
        before["opportunity"] = store.get_opportunity(
            axes, _OPPORTUNITY, BINDING, collet_id
        )
        before["applications"] = store.list_applications(axes, BINDING, collet_id)
        if not happy:
            params["binding_id"] = "missing_binding"

    def check(response, world):
        store, axes = world.get(MethodStoreProtocol), current_axes()
        collet_id = params["collab_context_id"]
        opportunity = store.get_opportunity(axes, _OPPORTUNITY, BINDING, collet_id)
        applications = store.list_applications(axes, BINDING, collet_id)
        if not happy:
            assert opportunity == before["opportunity"]
            assert applications == before["applications"]
            return
        data = response.json()["data"]
        assert data["accepted"] and data["state"] == state
        assert data["method_binding_id"] == BINDING
        if resource == "opportunities":
            assert opportunity.state.value == state
            assert opportunity.transitioned_by == OWNER.actor.principal_id
        else:
            [application] = applications
            assert application.application_id == data["object_id"] == _APPLICATION
            assert application.state.value == state
            assert application.transitioned_by == OWNER.actor.principal_id
            if operation == "revoke":
                [decision] = store.list_decisions(axes, BINDING, collet_id)
                assert decision.application_ref == _APPLICATION

    endpoint_test(
        method="POST",
        path=BASE
        + "/{binding_id}/open/"
        + resource
        + "/{"
        + ("opportunity_id" if resource == "opportunities" else "application_id")
        + "}/"
        + operation,
        scenario="open_lifecycle_happy" if happy else "open_lifecycle_missing_binding",
        input=CaseInput(
            path_params=params,
            headers=HEADERS,
            json_body={"reason": "HTTP Open transition"},
        ),
        seed=seed,
        expect=ExpectSuccess(json_contains={"code": 200000})
        if happy
        else ExpectError(status=404, json_contains={"code": 404000}),
        extra_assertions=(check,),
    )(lambda: None)


for _resource, _operations in (
    ("opportunities", (("pause", "PAUSED"), ("reopen", "OPEN"), ("close", "CLOSED"))),
    (
        "applications",
        (("reject", "REJECTED"), ("withdraw", "WITHDRAWN"), ("revoke", "REVOKED")),
    ),
):
    for _operation, _state in _operations:
        for _happy in (True, False):
            _register(_resource, _operation, _state, _happy)
