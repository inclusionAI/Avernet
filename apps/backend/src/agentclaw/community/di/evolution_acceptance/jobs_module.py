"""Explicit legacy Worker acceptance assembly; never installed by Backend startup.

Registry/Engine integration tests opt into this graph. Its ephemeral signing key
and in-memory default are not production configuration. Canonical service wiring
and Worker HTTP live in apps/evolution.
"""

from __future__ import annotations

import os
import secrets

from injector import Injector, Module, inject, provider, singleton

from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from avernet_evolution.core.evolution_ledger import LocalExperimentLedger
from avernet_evolution.core.evolution_run.budget_service import BudgetService
from avernet_evolution.core.evolution_run.operation_service import OperationService
from agentclaw.community.plugins.evolution.genome_registry import RegistryGenomePort
from avernet_evolution.core.evolution_run.job_protocol import JobChannel, JobTokenIssuer
from avernet_evolution.core.evolution_run.job_service import JobService
from avernet_evolution.core.evolution_run.store import EvolutionStore
from avernet_evolution.core.evolution_run.ports import ExperienceQueryFactory
from avernet_evolution.core.evolution_strategy.protocols import Models
from agentclaw.community.di.evolution_acceptance.runtime_module import _resolve

DEFAULT_DB_PATH = ":memory:"


class EvolutionJobsModule(Module):
    """Explicit acceptance graph; ephemeral keys are not restart-safe production keys."""

    @singleton
    @provider
    def evolution_store(self) -> EvolutionStore:
        return EvolutionStore(os.environ.get("EVOLUTION_DB_PATH", DEFAULT_DB_PATH))

    @singleton
    @provider
    @inject
    def evolution_jobs(self, store: EvolutionStore) -> JobService:
        return JobService(store)

    @singleton
    @provider
    def evolution_tokens(self) -> JobTokenIssuer:
        """进程内随机密钥：令牌只注入沙箱环境，从不持久化。"""

        signing_key = secrets.token_bytes(32)
        return JobTokenIssuer(secret=signing_key)

    @singleton
    @provider
    @inject
    def evolution_genome(self, registry: GenomeRegistryProtocol) -> RegistryGenomePort:
        return RegistryGenomePort(registry=registry)

    @singleton
    @provider
    @inject
    def job_channel(
        self,
        store: EvolutionStore,
        jobs: JobService,
        genome: RegistryGenomePort,
        tokens: JobTokenIssuer,
        ledger: LocalExperimentLedger,
        budget: BudgetService,
        operations: OperationService,
        injector: Injector,
    ) -> JobChannel:
        return JobChannel(
            store=store,
            jobs=jobs,
            genome=genome,
            tokens=tokens,
            ledger=ledger,
            budget=budget,
            operations=operations,
            models=_resolve(injector, Models, None),
            experience_factory=_resolve(injector, ExperienceQueryFactory, None),
        )
