from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from avernet_genome.mutation_scope import FrozenMutationScope
from avernet_genome.patch_models import GenomePatch

from avernet_evolution.core.evolution_strategy.models import (
    Budget,
    Candidate,
    Completion,
    Operation,
    RunSummary,
    Verdict,
)


@runtime_checkable
class EvolutionStrategy(Protocol):
    """The sole strategy plugin port: one dispatch returns one summary."""

    async def run(self, ctx: StrategyContext) -> RunSummary: ...


class GenomeRevisionView(Protocol):
    id: str
    seq: str
    spec: dict[str, Any]

    @property
    def paths(self) -> tuple[str, ...]: ...

    async def read(self, path: str) -> bytes: ...
    async def lineage(self, depth: int = 10) -> list[str]: ...


class Workspace(Protocol):
    id: str
    base: str

    async def read(self, path: str) -> bytes: ...
    async def write(self, path: str, content: bytes) -> None: ...
    async def to_patch(self, *, rationale: str, evidence: tuple[str, ...] = ()) -> GenomePatch: ...


class WorkspaceFactory(Protocol):
    async def materialise(self, revision: str, *, key: str) -> Workspace: ...


class Candidates(Protocol):
    async def submit(self, candidate: Candidate) -> str: ...
    async def verdict(self, candidate_id: str) -> Verdict: ...


class Models(Protocol):
    async def complete(
        self, *, messages: list[Any], model: str | None = None, max_tokens: int = 4096
    ) -> Completion: ...


class ExperienceQuery(Protocol):
    async def sessions(self, *, days: int, limit: int = 500, revision: str | None = None) -> list[Any]: ...
    async def feedback(self, *, days: int, kinds: list[str] | None = None, limit: int = 500) -> list[Any]: ...


class AgentRunner(Protocol):
    async def start(
        self,
        definition: str,
        *,
        workspace: Workspace,
        prompt: str,
        idempotency_key: str,
        timeout_s: int = 1800,
    ) -> str: ...


class TrainEvaluator(Protocol):
    async def add_train_cases(self, cases: list[dict[str, Any]]) -> list[str]: ...
    async def start_train(self, workspace: Workspace, *, idempotency_key: str) -> str: ...


class Operations(Protocol):
    async def get(self, op_id: str) -> Operation: ...
    async def cancel(self, op_id: str) -> None: ...
    async def wait(self, op_id: str, *, poll_s: float = 5) -> Operation: ...


class BudgetMeter(Protocol):
    def remaining(self) -> Budget: ...
    async def charge(self, usd: float, reason: str) -> None: ...


class RunLog(Protocol):
    async def info(self, message: str, **fields: object) -> None: ...
    async def warn(self, message: str, **fields: object) -> None: ...


class ArtifactSink(Protocol):
    async def put(self, name: str, content: bytes) -> str: ...


class CancellationToken(Protocol):
    def is_set(self) -> bool: ...


class StrategyContext(Protocol):
    run_id: str
    params: dict[str, Any]
    attempt: int
    granted: list[str]
    mutation_scope: FrozenMutationScope | None

    parent: GenomeRevisionView
    workspace: WorkspaceFactory
    budget: BudgetMeter
    log: RunLog
    artifacts: ArtifactSink
    cancelled: CancellationToken
    operations: Operations
    candidates: Candidates
    models: Models
    experience: ExperienceQuery
    agents: AgentRunner
    evaluation: TrainEvaluator
