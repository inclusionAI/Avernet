"""L5-owned read-only policy and resource-validation capabilities.

No dispatch or store-write authority is provided to either validator.
"""

from typing import Protocol, runtime_checkable
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.artifact_manifest import (
    ArtifactPortBinding,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    Participant,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import Stage
from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
)


@runtime_checkable
class SwarmWorkflowPolicyReader(Protocol):
    def workflow_for(
        self, collet_id: str, binding_id: str
    ) -> SwarmWorkflowDefinition | None: ...


class UnmanagedSwarmPolicies:
    """Explicit adapter for standalone non-product Method assemblies/tests only."""

    def workflow_for(self, collet_id: str, binding_id: str) -> None:
        return None


@runtime_checkable
class SwarmArtifactEvidenceValidator(Protocol):
    """Validate real content and provider-guaranteed immutable revision; raise on failure."""

    def validate_outputs(
        self, stage: Stage, output_bindings: tuple[ArtifactPortBinding, ...]
    ) -> None: ...
    def validate_inputs(self, stage: Stage, participant: Participant) -> None: ...


class UnavailableSwarmArtifactValidator:
    """Fail closed until the host installs an immutable-resource/access adapter."""

    def validate_outputs(self, stage, output_bindings):
        from agentclaw.community.core.task.context_collab.errors import (
            ContextCollabValidationError,
        )

        raise ContextCollabValidationError(
            "workflow artifact validation capability is not installed"
        )

    def validate_inputs(self, stage, participant):
        if stage.input_bindings or stage.input_artifact_refs:
            from agentclaw.community.core.task.context_collab.errors import (
                ContextCollabValidationError,
            )

            raise ContextCollabValidationError(
                "workflow resource access capability is not installed"
            )
