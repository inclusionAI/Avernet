"""Public reproducible workflow fixture: actual DI/SQLite, local versioned files.

This provider only promises immutable revisions addressed by their content hash;
changing file bytes invalidates that version rather than silently reading latest.
"""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from injector import AssistedBuilder, Injector, InstanceProvider

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProductProfilePublicationAuthorizer,
    ProfilePublisherAllowlist,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.artifact_manifest import (
    ArtifactPortBinding,
    artifact_refs,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageExecutionReport,
    StageReportProtocol,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ResourceBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmWorkflowPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver import (
    SwarmDriver,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    StageExecutionService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
    SwarmWorkflowRef,
    SwarmWorkflowNode,
    SwarmArtifactOutput,
    SwarmArtifactInput,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow_ports import (
    SwarmArtifactEvidenceValidator,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
    MethodCompositionTemplate,
    MethodTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateNewCommand,
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.product_profile_publication import (
    ProductProfilePublishCommand,
    ProductProfilePublicationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
    ParticipantScopeKind,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin

AGENTS = (AgentRef("bot", "workflow_a"), AgentRef("bot", "workflow_b"))
OWNER = ActorContext(PrincipalRef("human", "workflow_owner"), "test")
PUBLISHER = ActorContext(PrincipalRef("system", "workflow_publisher"), "test")


def node(node_id, depends_on=(), inputs=(), agents=AGENTS):
    return SwarmWorkflowNode(
        node_id,
        node_id,
        f"Perform complete objective for {node_id}",
        agents,
        "input/1",
        "output/1",
        "completed/1",
        (SwarmArtifactOutput("result", "validated result", "document", "text/1"),),
        inputs,
        depends_on,
    )


def workflow():
    return SwarmWorkflowDefinition(
        SwarmWorkflowRef("delivery", 1),
        (
            node("prepare"),
            node(
                "build",
                ("prepare",),
                (SwarmArtifactInput("requirements", "prepare", "result"),),
            ),
            node(
                "review", ("prepare",)
            ),  # Pure control dependency; no implicit data handoff.
            node(
                "finalize",
                ("build", "review"),
                (
                    SwarmArtifactInput("code", "build", "result"),
                    SwarmArtifactInput("review", "review", "result"),
                ),
            ),
        ),
    )


def profile(dag=None, version=1):
    return create_product_profile(
        replace(
            TASK_COMMISSION_PROFILE,
            ref=VersionedProfileRef("workflow_delivery", version),
            composition=MethodCompositionTemplate(
                (
                    MethodTemplate(
                        get_profile("Swarm"), SwarmWorkflowPolicy(dag or workflow())
                    ),
                )
            ),
            participant_scope=ParticipantScope(ParticipantScopeKind.ALLOWLIST, AGENTS),
        )
    )


class LocalEvidenceValidator:
    """Read-only external-content/access adapter with inspectable consumer hits."""

    def __init__(self):
        self.output_checks, self.input_checks = [], []
        self.revoked = set()

    @staticmethod
    def read(artifact):
        ref = artifact.resource_binding.resource_ref
        path = Path(ref.resource_id)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != ref.revision:
            raise ContextCollabValidationError("immutable artifact version unavailable")
        return path.read_text()

    def validate_outputs(self, stage, bindings):
        self.output_checks.append((stage.stage_id, bindings))
        for output, binding in zip(stage.workflow_node.outputs, bindings):
            if output.content_contract_ref != "text/1" or any(
                not self.read(a).startswith(stage.objective) for a in binding.artifacts
            ):
                raise ContextCollabValidationError("artifact content contract failed")

    def validate_inputs(self, stage, participant):
        self.input_checks.append(
            (stage.stage_id, participant.agent_ref, stage.input_bindings)
        )
        if stage.workflow_node.input_artifact_refs:
            raise ContextCollabValidationError(
                "test provider has no external input-ref registry"
            )
        for binding in stage.input_bindings:
            for artifact in binding.artifacts:
                if (participant.agent_ref, artifact.artifact_id) in self.revoked:
                    raise ContextCollabValidationError("artifact read revoked")
                self.read(artifact)


class LocalWorker:
    def __init__(self, directory, validator, automatic=True):
        self.directory, self.validator, self.automatic = directory, validator, automatic
        self.calls, self.produced = [], []
        self.consumed = {}
        self.reports = (
            None  # Test assembly explicitly supplies the ingress before dispatch.
        )

    def execute(
        self,
        stage,
        participant,
        execution_profile,
        allocation_ref,
        collet_id,
        binding_id,
    ):
        self.calls.append(
            (
                stage,
                participant,
                execution_profile,
                allocation_ref,
                collet_id,
                binding_id,
            )
        )
        self.validator.validate_inputs(stage, participant)
        self.consumed[stage.stage_id] = tuple(
            (binding.name, tuple(self.validator.read(a) for a in binding.artifacts))
            for binding in stage.input_bindings
        )
        if self.automatic:
            self.finish()

    def produce(self):
        stage, participant, _, allocation_ref, collet_id, binding_id = self.calls[-1]
        bindings = []
        for output in stage.workflow_node.outputs:
            content = f"{stage.objective}\nResult: {output.name}\n"
            revision = sha256(content.encode()).hexdigest()
            path = self.directory / f"{stage.stage_id}-{output.name}-{revision}.txt"
            path.write_text(content)
            artifact = Artifact(
                f"artifact:{stage.stage_id}:{output.name}",
                ResourceBinding(
                    ResourceRef(output.resource_type, str(path), revision), None
                ),
            )
            bindings.append(ArtifactPortBinding(output.name, (artifact,)))
        report = StageExecutionReport(
            collet_id,
            binding_id,
            stage.stage_id,
            allocation_ref,
            artifact_refs(bindings),
            output_bindings=tuple(bindings),
        )
        self.produced.append(report)
        return report, ActorContext(
            PrincipalRef(
                participant.agent_ref.agent_type, participant.agent_ref.agent_id
            ),
            "worker",
        )

    def finish(self):
        report, actor = self.produce()
        self.reports.report(report, actor)

    def accepts(self, stage, report):
        self.validator.validate_outputs(stage, report.output_bindings)
        return True


def deploy(db, directory, definition=None, automatic=True):
    definition = definition or profile()
    validator = LocalEvidenceValidator()
    worker = LocalWorker(directory, validator, automatic)

    def bindings(binder):
        binder.bind(DatabasePlugin, to=db)
        binder.bind(
            ProductProfilePublicationAuthorizer,
            to=ProfilePublisherAllowlist((PUBLISHER.actor,)),
        )
        binder.bind(SwarmArtifactEvidenceValidator, to=InstanceProvider(validator))
        binder.bind(StageExecutionService, to=InstanceProvider(worker))

    injector = Injector([TaskPersistenceModule(), TaskModule(), bindings])
    injector.get(ProductProfilePublicationServiceProtocol).publish(
        ProductProfilePublishCommand(definition), PUBLISHER
    )
    worker.reports = injector.get(StageReportProtocol)
    instance = injector.get(ProductInstantiationServiceProtocol).instantiate(
        ProductInstantiateNewCommand(
            ColletCreateCommand(OWNER.actor, title="DAG delivery"),
            definition.ref,
            participant_refs=AGENTS,
            executor_ref=AGENTS[-1],
        ),
        OWNER,
    )
    repository = injector.get(ContextCollabRepositoryProtocol)
    record = repository.get_current_status(instance.collet_ref)
    injector.get(ContextServiceProtocol).create_task(
        instance.collet_ref,
        TaskCreateCommand(
            record.collet.version,
            OWNER.actor,
            objective="Deliver a verified DAG result",
        ),
        OWNER,
    )
    driver = injector.get(AssistedBuilder[SwarmDriver]).build(
        collet_id=instance.collet_ref
    )
    return injector, driver, worker, validator
