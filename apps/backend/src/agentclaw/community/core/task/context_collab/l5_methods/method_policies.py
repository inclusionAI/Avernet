"""L5-owned Method policy values for composition by callers, including L6."""

from agentclaw.community.core.task.context_collab.l5_methods.swarm_workflow import (
    SwarmWorkflowDefinition,
)

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    OpenPublicationProfile,
)


@dataclass(frozen=True)
class OpenMethodPolicy:
    """Publication/admission policy for an Open Method."""

    publication: OpenPublicationProfile
    method: Literal["Open"] = field(default="Open", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.publication, OpenPublicationProfile):
            raise ContextCollabValidationError(
                "Open Method policy requires an OpenPublicationProfile"
            )


@dataclass(frozen=True)
class SwarmMethodPolicy:
    """Explicit planning capability selection for autonomous Swarm coordination."""

    plan_service_ref: str
    method: Literal["Swarm"] = field(default="Swarm", init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.plan_service_ref, str)
            or not self.plan_service_ref.strip()
        ):
            raise ContextCollabValidationError(
                "Swarm plan capability requires a non-empty reference"
            )


@dataclass(frozen=True)
class SwarmWorkflowPolicy:
    """Deterministic graph, random eligible executor per committed allocation."""

    workflow: SwarmWorkflowDefinition
    method: Literal["Swarm"] = field(default="Swarm", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.workflow, SwarmWorkflowDefinition):
            raise ContextCollabValidationError("Swarm workflow requires a typed DAG")


@dataclass(frozen=True)
class SwarmStagesPolicy:
    """Capabilities for YAML-owned stages, never a second authored stage graph."""
    execution_ref: str
    acceptance_ref: str
    overall_acceptance: Literal["stages", "owner"]
    method: Literal["Swarm"] = field(default="Swarm", init=False)

    def __post_init__(self) -> None:
        if any(type(v) is not str or not v.strip() for v in (self.execution_ref, self.acceptance_ref)):
            raise ContextCollabValidationError("stage execution/acceptance capability refs are required")
        if self.overall_acceptance not in {"stages", "owner"}:
            raise ContextCollabValidationError("overall acceptance must be explicit")


MethodPolicy: TypeAlias = SwarmStagesPolicy | OpenMethodPolicy | SwarmMethodPolicy | SwarmWorkflowPolicy
