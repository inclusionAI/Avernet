"""进程内 StrategyContext（06-evolution-run.zh-CN §5.1、§9；实现 03 §2.5）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.evolution_strategy import (
    AggregateScores,
    Candidate,
    Budget as SdkBudget,
    Completion,
    Operation as SdkOperation,
    OperationKind as SdkOperationKind,
    OperationStatus,
    Verdict,
    VerdictStatus,
)
from agentclaw.community.core.evolution_strategy.local import (
    LocalWorkspace,
    StaticRevision,
)

from .budget import BudgetUsage
from .enums import ChargeSource
from agentclaw.community.core.evolution_strategy.errors import CapabilityNotGranted
from .records import Run
from .store import EvolutionStore, utcnow
from .submission import submit_candidate

__all__ = ["RunStrategyContext", "build_run_context"]


class _ParentView:
    def __init__(self, revision_id: str, files: dict[str, bytes]) -> None:
        self.id = revision_id
        self.seq = revision_id
        self.spec: dict[str, Any] = {}
        self._files = files

    async def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError as exc:
            raise FileNotFoundError(path) from exc

    async def lineage(self, depth: int = 10) -> list[str]:
        return [self.id]


class _WorkspaceFactory:
    """按 (run, key) 幂等；沙箱文件由基因组快照构造。"""

    def __init__(
        self, store: EvolutionStore, run: Run, genome, base_files: dict[str, bytes]
    ) -> None:
        self._store = store
        self._run = run
        self._genome = genome
        self._base = base_files
        self._made: dict[str, LocalWorkspace] = {}

    async def materialise(self, revision: str, *, key: str) -> LocalWorkspace:
        if key in self._made:
            return self._made[key]
        record = self._store.find_workspace(self._run.id, key)
        files = (
            self._base
            if revision == self._run.parent_revision
            else await self._snapshot(revision)
        )
        base = StaticRevision(
            id=revision,
            seq=revision,
            files={
                path: data.decode("utf-8", "replace") for path, data in files.items()
            },
        )
        ws = LocalWorkspace(
            base, workspace_id=f"ws_{self._run.id}_{abs(hash(key)) % 10_000}"
        )
        self._made[key] = ws
        if record is None:
            from .records import Workspace

            self._store.insert_workspace(
                Workspace(
                    id=ws.id,
                    run_id=self._run.id,
                    key=key,
                    revision=revision,
                    created_at=utcnow(),
                )
            )
        return ws

    async def _snapshot(self, revision: str) -> dict[str, bytes]:
        rev = await self._genome.resolve_revision(self._run.bot, revision)
        # GenomePort.snapshot_files 的生产适配器（RegistryGenomePort）需要 bot 才能
        # 按租户/租户内容存储读取字节；不传 bot 会在真实链路上 TypeError。
        return await self._genome.snapshot_files(rev, bot=self._run.bot)


class _Candidates:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    async def submit(self, candidate: Candidate) -> str:
        return await self._ctx._submit(candidate)

    async def verdict(self, candidate_id: str) -> Verdict:
        verifier = self._ctx._verifier
        if verifier is not None:
            view = await verifier.strategy_view(candidate_id, self._ctx._run.id)
            return Verdict(
                candidate=candidate_id,
                status=VerdictStatus(view.status.value),
                revision=view.revision,
                aggregates={
                    name: AggregateScores(
                        cases=aggregate.cases,
                        seeds=aggregate.seeds,
                        mean_delta=aggregate.mean_delta,
                        ci=aggregate.ci,
                    )
                    for name, aggregate in view.aggregates.items()
                },
                reasons=tuple(reason.value for reason in view.reasons),
            )
        for row in self._ctx._store.candidates(self._ctx._run.id):
            if row.candidate_id == candidate_id:
                return Verdict(
                    candidate=candidate_id, status=VerdictStatus(row.verdict)
                )
        return Verdict(candidate=candidate_id, status=VerdictStatus.PENDING)


class _Models:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    async def complete(
        self, *, messages: list[Any], model: str | None = None, max_tokens: int = 4096
    ) -> Completion:
        provider = self._ctx._models_provider
        if provider is None:
            raise NotImplementedError("no models provider configured")
        completion = await provider.complete(
            messages=messages, model=model, max_tokens=max_tokens
        )
        cost = getattr(completion, "cost", None)
        usage = BudgetUsage.of(
            usd=getattr(cost, "usd", 0) if cost else 0,
            tokens=getattr(completion, "tokens", 0),
        )
        await self._ctx._budget.charge(
            self._ctx._run.id, usage, ChargeSource.MODEL_CALL
        )
        return completion


class _Experience:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    async def sessions(
        self, *, days: int, limit: int = 500, revision: str | None = None
    ) -> list[Any]:
        provider = self._ctx._experience_provider
        if provider is None:
            raise NotImplementedError("no experience provider configured")
        return await provider.sessions(days=days, limit=limit, revision=revision)

    async def feedback(
        self, *, days: int, kinds: list[str] | None = None, limit: int = 500
    ) -> list[Any]:
        provider = self._ctx._experience_provider
        if provider is None:
            raise NotImplementedError("no experience provider configured")
        return await provider.feedback(days=days, kinds=kinds, limit=limit)


class _Agents:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    async def start(
        self,
        definition: str,
        *,
        workspace: Any,
        prompt: str,
        idempotency_key: str,
        timeout_s: int = 1800,
    ) -> str:
        provider = self._ctx._agents_provider
        if provider is None:
            raise NotImplementedError("no agents provider configured")
        return await provider.start(
            definition=definition,
            workspace=workspace,
            prompt=prompt,
            idempotency_key=idempotency_key,
            timeout_s=timeout_s,
            run_id=self._ctx._run.id,
        )


class _Evaluation:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    async def add_train_cases(self, cases: list[dict[str, Any]]) -> list[str]:
        provider = self._ctx._evaluation_provider
        if provider is None:
            raise NotImplementedError("no evaluation provider configured")
        return await provider.add_train_cases(
            cases, run_id=self._ctx._run.id
        )

    async def start_train(self, workspace: Any, *, idempotency_key: str) -> str:
        provider = self._ctx._evaluation_provider
        if provider is None:
            raise NotImplementedError("no evaluation provider configured")
        return await provider.start_train(
            workspace=workspace,
            idempotency_key=idempotency_key,
            run_id=self._ctx._run.id,
        )


class _Operations:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    async def get(self, op_id: str) -> SdkOperation:
        record = await self._ctx._operations.get(self._ctx._run.id, op_id)
        return SdkOperation(
            id=record.id,
            kind=SdkOperationKind(record.kind.value),
            status=OperationStatus(record.status.value),
            result=record.result,
            error=record.error,
            cost_usd=float(record.cost.usd),
        )

    async def cancel(self, op_id: str) -> None:
        await self._ctx._operations.cancel(self._ctx._run.id, op_id)

    async def wait(self, op_id: str, *, poll_s: float = 5) -> SdkOperation:
        import asyncio

        while True:
            operation = await self.get(op_id)
            if operation.status in (
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.CANCELLED,
            ):
                return operation
            await asyncio.sleep(poll_s)


class _BudgetMeter:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    def remaining(self) -> SdkBudget:
        run = self._ctx._store.get_run(self._ctx._run.id)
        used = run.budget_used if run is not None else BudgetUsage()
        budget = run.budget if run is not None else self._ctx._run.budget
        return SdkBudget(
            max_usd=budget.max_usd,
            max_wall_clock_s=budget.max_wall_clock_s,
            spent_usd=float(used.usd),
            tokens=used.tokens,
            max_rollouts=budget.max_rollouts,
            max_tokens=budget.max_tokens,
        )

    async def charge(self, usd: float, reason: str) -> None:
        await self._ctx._budget.charge(
            self._ctx._run.id, BudgetUsage.of(usd=usd), ChargeSource.EXPLICIT
        )


class _Log:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx
        self.entries: list[tuple[str, str, dict[str, Any]]] = []

    async def info(self, message: str, **fields: object) -> None:
        self.entries.append(("info", message, dict(fields)))

    async def warn(self, message: str, **fields: object) -> None:
        self.entries.append(("warn", message, dict(fields)))


class _Artifacts:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx
        self.blobs: dict[str, bytes] = {}

    async def put(self, name: str, content: bytes) -> str:
        import secrets

        artifact_id = "art_" + secrets.token_hex(4)
        self.blobs[artifact_id] = content
        return artifact_id


class _Cancellation:
    def __init__(self, ctx: "RunStrategyContext") -> None:
        self._ctx = ctx

    def is_set(self) -> bool:
        run = self._ctx._store.get_run(self._ctx._run.id)
        return bool(run and run.cancel_requested)


@dataclass
class RunStrategyContext:
    """一次运行尝试的真实上下文；访问未授予的能力抛 CapabilityNotGranted。"""

    run_id: str
    params: dict[str, Any]
    attempt: int
    granted: list[str]
    parent: _ParentView
    workspace: _WorkspaceFactory
    budget: _BudgetMeter
    log: _Log
    artifacts: _Artifacts
    cancelled: _Cancellation
    operations: _Operations
    candidates: _Candidates
    models: _Models
    experience: _Experience
    agents: _Agents
    evaluation: _Evaluation

    _run: Run = field(repr=False)
    _store: EvolutionStore = field(repr=False)
    _genome: Any = field(repr=False)
    _budget: Any = field(repr=False)
    _operations: Any = field(repr=False)
    _promotion: Any = field(repr=False)
    _ledger: Any = field(repr=False)
    _verifier: Any = field(repr=False)
    _models_provider: Any = field(repr=False, default=None)
    _experience_provider: Any = field(repr=False, default=None)
    _agents_provider: Any = field(repr=False, default=None)
    _evaluation_provider: Any = field(repr=False, default=None)
    _registration: Any = field(repr=False, default=None)

    def requires(self, capability: str) -> None:
        if capability not in self.granted:
            raise CapabilityNotGranted(capability)

    async def _submit(self, candidate: Candidate) -> str:
        self.requires("candidates@1")
        return await submit_candidate(
            store=self._store,
            genome=self._genome,
            run=self._run,
            candidate=candidate,
            promotion=self._promotion,
            ledger=self._ledger,
            verifier=self._verifier,
        )


async def build_run_context(
    run: Run,
    *,
    store: EvolutionStore,
    genome,
    budget,
    operations,
    promotion=None,
    ledger=None,
    verifier=None,
    models_provider=None,
    experience_provider=None,
    agents_provider=None,
    evaluation_provider=None,
    registration=None,
) -> RunStrategyContext:
    """按运行冻结的输入构建上下文（§5.1）；授予能力已记录在 `run.granted`。"""

    revision = await genome.resolve_revision(run.bot, run.parent_revision)
    files = await genome.snapshot_files(revision, bot=run.bot)
    parent = _ParentView(run.parent_revision, files)
    workspace = _WorkspaceFactory(store, run, genome, files)
    context = RunStrategyContext(
        run_id=run.id,
        params=dict(run.params),
        attempt=run.attempt,
        granted=list(run.granted),
        parent=parent,
        workspace=workspace,
        budget=None,
        log=None,
        artifacts=None,
        cancelled=None,
        operations=None,
        candidates=None,
        models=None,
        experience=None,
        agents=None,
        evaluation=None,
        _run=run,
        _store=store,
        _genome=genome,
        _budget=budget,
        _operations=operations,
        _promotion=promotion,
        _ledger=ledger,
        _verifier=verifier,
        _models_provider=models_provider,
        _experience_provider=experience_provider,
        _agents_provider=agents_provider,
        _evaluation_provider=evaluation_provider,
        _registration=registration,
    )
    context.budget = _BudgetMeter(context)
    context.log = _Log(context)
    context.artifacts = _Artifacts(context)
    context.cancelled = _Cancellation(context)
    context.operations = _Operations(context)
    context.candidates = _Candidates(context)
    context.models = _Models(context)
    context.experience = _Experience(context)
    context.agents = _Agents(context)
    context.evaluation = _Evaluation(context)
    return context
