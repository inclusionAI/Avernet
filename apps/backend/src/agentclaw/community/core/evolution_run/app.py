"""组合根：把运行、验证、晋升、实验记录与调度器装在一起（06 §15、08 §9、07 §9）。

所有外部依赖以端口注入；本函数只做装配，不含决策逻辑。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .bindings_service import BindingsService
from .container_host import ContainerHost
from .job_protocol import JobChannel, JobTokenIssuer
from .budget_service import BudgetService
from .host import InProcessHost
from .job_service import JobService
from .operation_service import OperationService
from .ports import VerdictStatus
from .records import Run
from .run_service import RunService
from .scheduler import TriggerScheduler
from .store import EvolutionStore

__all__ = ["EvolutionRuntime", "build_evolution_runtime"]


@dataclass
class EvolutionRuntime:
    store: EvolutionStore
    bindings: BindingsService
    runs: RunService
    jobs: JobService
    operations: OperationService
    budget: BudgetService
    ledger: Any
    verification: Any
    promotion: Any
    host: Any
    scheduler: TriggerScheduler
    channel: JobChannel


def build_evolution_runtime(
    *,
    genome: Any,
    registry: Any,
    governance: Any,
    kills: Any,
    strategies: dict[str, Callable[[], Any]],
    profiles: dict[str, Any] | None = None,
    cases_for: Callable[[Any, Any], Awaitable[Any]] | None = None,
    execute: Callable[[Any, str, Any, int], Awaitable[Any]] | None = None,
    grade: Callable[[Any, Any], Awaitable[Any]] | None = None,
    agreement: Callable[[Any], Awaitable[float | None]] | None = None,
    models: Any = None,
    experience: Any = None,
    agents_provider: Any = None,
    evaluation_provider: Any = None,
    apply: Callable[[Any, str], Awaitable[Any]] | None = None,
    sandbox_worker: Any = None,
    strategy_images: Callable[[Any], str] | None = None,
    job_endpoint: str = "http://127.0.0.1:8000/openapi/v1/evolution/jobs",
    patch_of: Callable[[str], Awaitable[Any]] | None = None,
    database: str | Path = ":memory:",
    lease_seconds: int = 60,
    heartbeat_s: float = 20.0,
) -> EvolutionRuntime:
    from agentclaw.community.core.evolution_ledger import LocalExperimentLedger
    from agentclaw.community.core.evolution_promote import (
        CandidateFacts,
        LocalPromotionService,
    )
    from agentclaw.community.core.evolution_verify import LocalVerificationService

    store = EvolutionStore(database)
    ledger = LocalExperimentLedger(
        ":memory:" if str(database) == ":memory:" else f"{database}.ledger"
    )
    budget = BudgetService(store, governance=governance)
    bindings = BindingsService(
        store, registry=registry, genome=genome, governance=governance, kills=kills
    )
    runs = RunService(
        store,
        bindings=bindings,
        registry=registry,
        genome=genome,
        budget=budget,
        kills=kills,
        ledger=ledger,
    )
    jobs = JobService(store, lease_seconds=lease_seconds)
    operations = OperationService(store, budget=budget)
    verification = LocalVerificationService(
        profiles=profiles or {},
        cases_for=cases_for or _no_cases,
        execute=execute or _no_execute,
        grade=grade or _no_grade,
        agreement=agreement,
    )

    async def facts(candidate_id: str) -> CandidateFacts:
        found = store.find_candidate(candidate_id)
        if found is None:
            raise KeyError(candidate_id)
        run = store.get_run(found["run_id"])
        assert run is not None
        try:
            verdict = await verification.get_verdict(
                candidate_id, run.verification_profile
            )
        except KeyError:
            verdict = None  # 底线在验证之前运行：此刻判定还是 pending
        binding = _binding_for(store, run)
        return await _facts_from(run, found, verdict, patch_of, genome, binding)

    promotion = LocalPromotionService(genome=genome, facts=facts, apply=apply)

    def ctx_factory(run: Run, **kwargs: Any):
        from .context import build_run_context

        return build_run_context(
            run,
            store=store,
            genome=genome,
            budget=budget,
            operations=operations,
            **kwargs,
        )

    signing_key = secrets.token_bytes(32)
    issuer = JobTokenIssuer(secret=signing_key)
    channel = JobChannel(
        store=store,
        jobs=jobs,
        genome=genome,
        promotion=promotion,
        ledger=ledger,
        verifier=verification,
        tokens=issuer,
        models=models,
        experience=experience,
        agents=agents_provider,
        evaluation=evaluation_provider,
        operations=operations,
        budget=budget,
    )
    if sandbox_worker is not None:
        if strategy_images is None:
            raise ValueError("container execution needs a strategy image resolver")
        host: Any = ContainerHost(
            sandbox=sandbox_worker,
            store=store,
            jobs=jobs,
            runs=runs,
            image_of=strategy_images,
            job_endpoint=job_endpoint,
            strategy_refs=tuple(strategies),
            tokens=issuer,
        )
    else:
        host = InProcessHost(
            strategies=strategies,
            store=store,
            jobs=jobs,
            operations=operations,
            budget=budget,
            genome=genome,
            ctx_factory=ctx_factory,
            promotion=promotion,
            ledger=ledger,
            verifier=verification,
            models_provider=models,
            experience_provider=experience,
            agents_provider=agents_provider,
            evaluation_provider=evaluation_provider,
            heartbeat_s=heartbeat_s,
        )
    scheduler = TriggerScheduler(store=store, runs=runs, genome=genome, kills=kills)
    return EvolutionRuntime(
        store=store,
        bindings=bindings,
        runs=runs,
        jobs=jobs,
        operations=operations,
        budget=budget,
        ledger=ledger,
        verification=verification,
        promotion=promotion,
        host=host,
        scheduler=scheduler,
        channel=channel,
    )


async def _facts_from(
    run: Run,
    found: dict[str, Any],
    verdict: Any,
    patch_of: Callable[[str], Awaitable[Any]] | None,
    genome: Any,
    binding: Any,
) -> Any:
    from agentclaw.community.core.evolution_promote import CandidateFacts
    from agentclaw.community.core.evolution_verify import Split

    splits = getattr(verdict, "splits", {}) or {}
    regression = splits.get(Split.REGRESSION)
    safety = splits.get(Split.SAFETY)
    patch = await patch_of(found["candidate_id"]) if patch_of is not None else None
    policy = await _policy_of(genome, run) if patch is not None else None
    return CandidateFacts(
        candidate=found["candidate_id"],
        revision=found["revision"],
        parent_revision=run.parent_revision,
        patch=patch,
        policy=policy,
        allowed_genes=tuple(run.allowed_genes),
        verdict=VerdictStatus(verdict.status).value
        if verdict is not None
        else "pending",
        regression_newly_failing=len(regression.newly_failing) if regression else 0,
        regression_tolerance=getattr(binding, "regression_tolerance", 1),
        safety_newly_failing=len(safety.newly_failing) if safety else 0,
        auto_promote_ceiling=_ceiling(binding),
    )


def _binding_for(store: EvolutionStore, run: Run) -> Any:
    document = store.get_bindings(run.bot.owner_id, run.bot.bot_id)
    if document is None:
        return None
    from .bindings_service import _bindings_from_document

    return next(
        (
            b
            for b in _bindings_from_document(document["document"])
            if b.id == run.binding_id
        ),
        None,
    )


def _ceiling(binding: Any) -> Any:
    from agentclaw.community.core.evolution_promote import RiskTier

    ceiling = getattr(binding, "auto_promote_ceiling", None)
    return RiskTier.T1 if ceiling is None else RiskTier(ceiling.value)


async def _policy_of(genome: Any, run: Run) -> Any:
    revision = await genome.resolve_revision(run.bot, run.parent_revision)
    return getattr(revision, "policy", None)


async def _no_cases(bot: Any, splits: Any) -> list[Any]:
    return []


async def _no_execute(bot: Any, revision: str, case: Any, seed: int) -> Any:
    raise NotImplementedError("no executor configured")


async def _no_grade(case: Any, rollout: Any) -> Any:
    raise NotImplementedError("no grader configured")
