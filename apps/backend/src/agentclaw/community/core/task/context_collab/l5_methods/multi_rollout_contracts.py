"""Durable controller facts and side-effect-free outbound strategy contracts.

All identities and decisions are local L5 records, never fabricated L4 Tasks.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..l2_agent_network.network_refs import AgentRef
from ..l4_context_primitives.product_status import AdjustTypeEnum
from .method_binding import MethodBinding
from .method_composition import MethodCompositionEdge
from .multi_rollout_policy import MultiRolloutMethodPolicy

SCHEMA = "product-runtime/multi-rollout@1"
FINAL_PHASES = {"SUCCEEDED", "FAILED", "STOPPED"}


@dataclass(frozen=True)
class StagePatch:
    stage: str
    instruction: str = ""
    candidates: tuple[AgentRef, ...] = ()


@dataclass(frozen=True)
class RunPlan:
    version: int
    digest: str
    profile_digest: str
    previous: str
    patches: tuple[StagePatch, ...] = ()
    rerun: tuple[str, ...] = ()
    evidence_digest: str = ""


@dataclass
class MultiRolloutCandidateRecord:
    rollout_id: int
    generation: int
    ordinal: int
    predecessor: int
    binding: MethodBinding
    edge: MethodCompositionEdge
    plan_digest: str
    namespace: str
    slot: bool = False
    started: bool = False
    incomplete: bool = False


@dataclass(frozen=True)
class StrategyDescriptor:
    reference: str
    rubric: str
    evaluator_ref: str
    minimum: int
    maximum: int


@dataclass(frozen=True)
class CandidateScore:
    rollout_id: int
    score: float
    reason: str


@dataclass(frozen=True)
class AdjustmentProposal:
    type: AdjustTypeEnum
    message: str
    patches: tuple[StagePatch, ...] = ()
    rerun: tuple[str, ...] = ()
    request_feedback: bool = False


@dataclass
class StrategyOperation:
    key: str
    payload_digest: str
    fence: str
    lease_until: int
    generation: int
    kind: str


@dataclass
class MultiRolloutRunRecord:
    run_id: str
    controller_binding: str
    body_binding: str
    nest_edge: str
    composition_digest: str
    policy: MultiRolloutMethodPolicy
    descriptor: StrategyDescriptor
    tenant_quota: int
    created_at: int
    deadline: int
    generation: int = 1
    phase: str = "CREATED"
    outcome: str = ""
    reason: str = ""
    winner: int = 0
    waiting_until: int = 0
    dispatches: int = 0
    strategy_calls: int = 0
    no_progress: int = 0
    failure_digest: str = ""
    candidates: list[MultiRolloutCandidateRecord] = field(default_factory=list)
    plans: list[RunPlan] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    operation: StrategyOperation | None = None
    strategy_attempts: list[dict] = field(default_factory=list)
    strategy_elapsed_ms: dict[str, int] = field(default_factory=dict)
    peak_concurrency: int = 0
    notifications: list[str] = field(default_factory=list)
    cancellations: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MultiRolloutSetup:
    descriptor: StrategyDescriptor
    tenant_quota: int


@runtime_checkable
class MultiRolloutStrategyPort(Protocol):
    def require(self, policy, stages, context) -> StrategyDescriptor:
        """Resolve exact versions; autonomous mode requires an explicit grant."""
        ...

    async def evaluate(
        self, descriptor, snapshot, qualified, context
    ) -> tuple[CandidateScore, ...]:
        """Trusted evaluator inspects actual artifacts under the frozen rubric."""
        ...

    async def propose_adjustment(
        self, reference, snapshot, feedback, context
    ) -> AdjustmentProposal: ...


@runtime_checkable
class MultiRolloutIsolationPort(Protocol):
    def require(self, reference, execution_ref, stages, context) -> int:
        """Attest independent session/workspace/artifacts and safe side effects.

        Return the installed tenant-wide quota (1..8), enforced atomically in SQL.
        A bare workspace name is NOT an isolation attestation.
        """
        ...

    async def prepare(self, reference, namespace, candidate, context) -> dict:
        """Idempotent preparation by namespace; immutable execution envelope."""
        ...

    async def verify_route(self, reference, namespace, route, context) -> None: ...
    async def verify_artifacts(
        self, reference, namespace, artifacts, context
    ) -> None: ...


@runtime_checkable
class MultiRolloutNotificationPort(Protocol):
    def require(self) -> None: ...
    async def notify(self, key, collet_id, rollout_id, deadline, context) -> None:
        """Host must deduplicate by key, including a crash after delivery."""
        ...


@runtime_checkable
class MethodRunPort(Protocol):
    def require(self, snapshot) -> None: ...
    async def start(self, collet_id, rollout_id, context) -> None: ...
    async def observe(self, collet_id, rollout_id, context) -> None: ...
    async def collect(self, collet_id, rollout_id, context): ...
    async def advance(self, collet_id, rollout_id, context) -> None: ...
    async def request_cancel(self, route) -> bool: ...
