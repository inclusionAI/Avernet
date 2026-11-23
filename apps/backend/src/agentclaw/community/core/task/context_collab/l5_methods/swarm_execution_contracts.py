"""Inbound Swarm execution Service API and outbound interaction planning contract.

No transport, L6 or runtime-session dependency. A session is the exact Stage's
Event-backed conversation. Decisions propose changes, never grant authority.
"""

from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    Stage,
    SwarmPlan,
)


class InteractionMode(str, Enum):
    IGNORE = "ignore"
    REPLAN_ALL = "replan_all"
    REPLAN_FROM = "replan_from"


@dataclass(frozen=True)
class StepCommand:
    request_id: str
    expected_version: int

    def __post_init__(self):
        if (
            not isinstance(self.request_id, str)
            or not self.request_id.strip()
            or len(self.request_id) > 128
        ):
            raise ContextCollabValidationError(
                "request_id must be nonempty and at most 128 characters"
            )
        if type(self.expected_version) is not int or self.expected_version < 1:
            raise ContextCollabValidationError("expected_version must be positive")


@dataclass(frozen=True)
class InteractionCommand(StepCommand):
    stage_ref: str
    session_ref: str
    content: str
    kind: str = "message"
    # Required only for replies; absent for a message/clarification request.
    clarification_ref: str | None = None

    def __post_init__(self):
        super().__post_init__()
        for name in ("stage_ref", "session_ref", "content"):
            if (
                not isinstance(getattr(self, name), str)
                or not getattr(self, name).strip()
            ):
                raise ContextCollabValidationError(f"{name} must be nonempty")
        if len(self.content) > 16000:
            raise ContextCollabValidationError("content exceeds 16000 characters")
        if not isinstance(self.kind, str) or self.kind not in {
            "message",
            "clarification_request",
            "clarification_reply",
        }:
            raise ContextCollabValidationError("invalid interaction kind")
        if (self.kind == "clarification_reply") != bool(self.clarification_ref):
            raise ContextCollabValidationError("only replies require clarification_ref")


@dataclass(frozen=True)
class InteractionPlanningRequest:
    collet_id: str
    binding_id: str
    plan: SwarmPlan
    stages: tuple[Stage, ...]
    stage_ref: str
    session_ref: str
    content: str
    interaction_ref: str


@dataclass(frozen=True)
class InteractionDecision:
    mode: InteractionMode
    reason: str
    # Exact Stage ID in the current revision, only for replan_from.
    from_stage_ref: str | None = None

    def validate(self, stages: tuple[Stage, ...]) -> None:
        if (
            not isinstance(self.mode, InteractionMode)
            or not isinstance(self.reason, str)
            or not self.reason.strip()
        ):
            raise ContextCollabValidationError("invalid interaction planner decision")
        if self.mode is InteractionMode.REPLAN_FROM:
            if not isinstance(self.from_stage_ref, str) or self.from_stage_ref not in {
                s.stage_id for s in stages
            }:
                raise ContextCollabValidationError(
                    "planner selected unknown replan Stage"
                )
        elif self.from_stage_ref is not None:
            raise ContextCollabValidationError("only replan_from may select a Stage")


class SwarmInteractionPlanner(Protocol):
    """Outbound capability; exceptions leave the message pending for explicit retry."""

    def decide(self, request: InteractionPlanningRequest) -> InteractionDecision: ...


class ConservativeInteractionPlanner:
    """Public-local default: no guessed language/keyword control commands."""

    def decide(self, request: InteractionPlanningRequest) -> InteractionDecision:
        return InteractionDecision(
            InteractionMode.IGNORE,
            "No interaction replanning capability installed; message retained",
        )


@runtime_checkable
class SwarmExecutionServiceProtocol(Protocol):
    """Caller -> core. Mutations require exact revision and stable request ID.

    Replay returns the original result after current authorization. Each step
    dispatches at most one new Stage; async work returns running, not success.
    stages() includes every planned node, actual manifests, edges and sessions.
    interact() resolves human feedback via the configured planner; worker-only
    clarification_request and owner-only clarification_reply share this API.
    """

    @abstractmethod
    def step(
        self, collet_id: str, binding_id: str, command: StepCommand, actor: ActorContext
    ) -> dict: ...

    @abstractmethod
    def stages(self, collet_id: str, binding_id: str, actor: ActorContext) -> dict: ...

    @abstractmethod
    def interact(
        self,
        collet_id: str,
        binding_id: str,
        command: InteractionCommand,
        actor: ActorContext,
    ) -> dict: ...


class SwarmDriverFactory(Protocol):
    """Composition-owned construction; caller cannot choose internal identities."""

    def build(self, collet_id: str): ...
