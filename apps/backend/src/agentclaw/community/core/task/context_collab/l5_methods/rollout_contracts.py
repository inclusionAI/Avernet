"""Lower-layer durable execution contracts; no dependency on L6 configuration."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum,
)
from agentclaw.community.core.task.context_collab.l5_methods.collet_rollout import (
    ColletRollout,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    MethodCompositionEdge,
)


@dataclass(frozen=True)
class CompiledStage:
    name: str
    candidates: tuple[AgentRef, ...]
    depends_on: tuple[str, ...]
    conditions: tuple[str, ...]


@dataclass(frozen=True)
class SessionRoute:
    provider: str
    session_id: str
    bot_id: str
    owner_id: str
    lifecycle_stage: str
    run_id: str


@dataclass
class StageRecord:
    stage_id: int
    attempt: str = ""
    lease_until: int = 0
    route: SessionRoute | None = None
    reused_from: int | None = None
    dispatch_state: str = "pending"


@dataclass
class RuntimeSnapshot:
    collet: Collet
    owner: PrincipalRef
    revision: int
    current_rollout_id: int
    rollouts: list[ColletRollout]
    records: list[StageRecord]
    specs: tuple[CompiledStage, ...]
    bindings: tuple[MethodBinding, ...]
    relations: tuple[MethodCompositionEdge, ...]
    execution_ref: str
    acceptance_ref: str
    overall_acceptance: str
    stopped: bool = False
    parents: dict[str, int] = field(default_factory=dict)

    def current(self) -> ColletRollout:
        return next(r for r in self.rollouts if r.rollout_id == self.current_rollout_id)


@dataclass(frozen=True)
class Adjustment:
    type: AdjustTypeEnum
    message: str
    stages: tuple[str, ...] = ()
    instruction: str = ""


@dataclass(frozen=True)
class ReportContext:
    actor: ActorContext
    request_id: str
    attempt: str
    expected_revision: int


@runtime_checkable
class ProductRuntimeAuthorization(Protocol):
    def check(
        self, action: str, context: ActorContext, owner: PrincipalRef
    ) -> None: ...
    def check_candidate(
        self, candidate: AgentRef, context: ActorContext, snapshot: RuntimeSnapshot
    ) -> None:
        """Check actual participation/resource authority, not YAML eligibility."""
        ...


@runtime_checkable
class RolloutExecutionPort(Protocol):
    def require(
        self, execution_ref: str, acceptance_ref: str, stages: tuple[CompiledStage, ...]
    ) -> None: ...
    async def start(
        self,
        execution_ref: str,
        candidate: AgentRef,
        content: str,
        attempt: str,
        context: ActorContext,
    ) -> SessionRoute: ...
    async def observe(self, route: SessionRoute) -> str:
        """Return RUNNING / RUN_FINISHED / RUN_FAILED from real runtime facts."""
        ...

    async def cancel(self, route: SessionRoute) -> bool: ...
    async def verify(
        self,
        acceptance_ref: str,
        route: SessionRoute,
        conditions: tuple[str, ...],
        artifacts: list[Artifact],
        acceptances: list[AcceptanceResult],
        context: ActorContext,
    ) -> None:
        """Raise on unverifiable evidence or untrusted acceptance identity."""
        ...


@runtime_checkable
class RolloutInteractionPort(Protocol):
    async def propose(
        self, snapshot: RuntimeSnapshot, content: str, context: ActorContext
    ) -> Adjustment: ...


@runtime_checkable
class StageSessionMessagesPort(Protocol):
    async def read(self, route: SessionRoute, limit: int) -> list[Any]: ...


@runtime_checkable
class RolloutRepository(Protocol):
    def get_collet(self, collet_id: int) -> RuntimeSnapshot: ...
    def locate(self, identity: int, kind: str) -> RuntimeSnapshot: ...
    def replay(
        self, request_id: str, operation: str, payload: dict, context: ActorContext
    ) -> dict | None: ...
    def save(
        self,
        snapshot: RuntimeSnapshot,
        revision: int,
        *,
        receipt: tuple[str, str, dict, ActorContext, dict] | None = None,
        intents: tuple[dict, ...] = (),
        successor: tuple[str, set[str]] | None = None,
    ) -> dict | None: ...
    def late_delivery(
        self, collet_id: int, stage_id: int, attempt: str, route: SessionRoute
    ) -> None: ...
    def pending_intents(self, limit: int = 20) -> list[dict]: ...
    def claim_intent(self, identity: int) -> bool: ...
    def finish_intent(self, identity: int, state: str) -> None: ...


@dataclass(frozen=True)
class ExecutionIdentity:
    bot_id: str
    owner_id: str
    lifecycle_stage: str


@runtime_checkable
class RolloutIdentityPort(Protocol):
    def resolve(
        self, candidate: AgentRef, context: ActorContext
    ) -> ExecutionIdentity: ...


@runtime_checkable
class RolloutEvidencePort(Protocol):
    def require(self, acceptance_ref: str) -> None: ...
    async def verify(
        self,
        acceptance_ref: str,
        route: SessionRoute,
        conditions: tuple[str, ...],
        artifacts: list[Artifact],
        acceptances: list[AcceptanceResult],
        context: ActorContext,
    ) -> None: ...
