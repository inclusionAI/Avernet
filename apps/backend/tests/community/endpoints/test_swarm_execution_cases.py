"""Registered happy/error cases exercise the full app's real Swarm service graph."""

from injector import InstanceProvider

from agentclaw.community.api.swarm_execution_service import (
    SwarmExecutionServiceProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    CollaborationReader,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    StageExecutionService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_execution_contracts import (
    StepCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow_ports import (
    SwarmArtifactEvidenceValidator,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateNewCommand,
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_profile_publication import (
    ProductProfilePublishCommand,
    ProductProfilePublicationServiceProtocol,
)
from tests.community.core.task.context_collab.swarm_workflow_support import (
    AGENTS,
    OWNER,
    PUBLISHER,
    LocalEvidenceValidator,
    profile,
)
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_PATH = "/api/v1/collaboration/task/context_collab/{collab_context_id}/method-bindings/{binding_id}/swarm"
_MESSAGE = "Please prioritize usability"


class PendingWorker:
    """Replace only the external execution capability, not business services."""

    def __init__(self):
        self.calls = []

    def execute(self, *args):
        self.calls.append(args)


def _seed_workflow(world):
    make_staff_user(world, user_id=OWNER.actor.principal_id)
    world.injector.binder.bind(
        ProductProfilePublicationAuthorizer,
        to=ProfilePublisherAllowlist((PUBLISHER.actor,)),
    )
    world.injector.binder.bind(
        StageExecutionService, to=InstanceProvider(PendingWorker())
    )
    world.injector.binder.bind(
        SwarmArtifactEvidenceValidator, to=InstanceProvider(LocalEvidenceValidator())
    )
    definition = profile()
    world.get(ProductProfilePublicationServiceProtocol).publish(
        ProductProfilePublishCommand(definition), PUBLISHER
    )
    instance = world.get(ProductInstantiationServiceProtocol).instantiate(
        ProductInstantiateNewCommand(
            ColletCreateCommand(OWNER.actor, title="HTTP DAG delivery"),
            definition.ref,
            participant_refs=AGENTS,
            executor_ref=AGENTS[-1],
        ),
        OWNER,
    )
    record = world.get(CollaborationReader).get_current_status(instance.collet_ref)
    world.get(ContextServiceProtocol).create_task(
        instance.collet_ref,
        TaskCreateCommand(
            record.collet.version,
            OWNER.actor,
            objective="Deliver a verified DAG result",
        ),
        OWNER,
    )
    [binding_id] = instance.method_binding_refs
    return instance.collet_ref, binding_id


def _register_case(method, suffix, happy):
    params, body = {}, {}

    def seed(world):
        collet_id, binding_id = _seed_workflow(world)
        service = world.get(SwarmExecutionServiceProtocol)
        graph = service.stages(collet_id, binding_id, OWNER)
        if suffix == "interactions":
            service.step(
                collet_id, binding_id, StepCommand("prepare", graph["version"]), OWNER
            )
            graph = service.stages(collet_id, binding_id, OWNER)
            stage = graph["stages"][0]
            body.update(
                stage_ref=stage["stage_id"],
                session_ref=stage["session_ref"],
                content=_MESSAGE,
            )
        params.update(
            collab_context_id=collet_id,
            binding_id=binding_id if happy else "unknown_binding",
        )
        body.update(request_id="http_control", expected_version=graph["version"])

    def check(response, world):
        worker = world.get(StageExecutionService)
        if not happy:
            assert len(worker.calls) == (1 if suffix == "interactions" else 0)
            return
        data = response.json()["data"]
        if suffix == "step":
            assert data["dispatched"] and len(worker.calls) == 1
        elif suffix == "stages":
            assert data["status"] == "UNPLANNED"
            assert len(data["stages"]) == len(data["edges"]) == 4
            assert not worker.calls
        else:
            assert data["mode"] == "ignore" and len(worker.calls) == 1
            graph = world.get(SwarmExecutionServiceProtocol).stages(
                params["collab_context_id"], params["binding_id"], OWNER
            )
            assert graph["stages"][0]["messages"][0]["content"] == _MESSAGE

    @endpoint_test(
        method=method,
        path=_PATH + "/" + suffix,
        scenario="swarm_execution_happy"
        if happy
        else "swarm_execution_unknown_binding",
        input=CaseInput(
            path_params=params,
            headers={"x-user-id": OWNER.actor.principal_id},
            json_body=body if method == "POST" else None,
        ),
        seed=seed,
        expect=ExpectSuccess(json_contains={"code": 200000})
        if happy
        else ExpectError(status=404, json_contains={"code": 404000}),
        extra_assertions=(check,),
    )
    def case():
        """The registered runner owns HTTP invocation and assertions."""


for _method, _suffix in (("POST", "step"), ("GET", "stages"), ("POST", "interactions")):
    for _happy in (True, False):
        _register_case(_method, _suffix, _happy)
