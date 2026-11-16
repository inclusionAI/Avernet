"""进化作业面（06 §7、§10）的组合：存储、作业服务、基因组适配、令牌与作业通道。

沙箱里的 worker 只经 `/openapi/v1/evolution/jobs` 与这里交互；`GenomePort` 由
production 注册表适配而来，因此候选修订与 bot 的基因组是同一条链上的记录。
"""

from __future__ import annotations

import os
import secrets

from injector import Module, inject, provider, singleton

from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.core.evolution_run.genome_registry_port import RegistryGenomePort
from agentclaw.community.core.evolution_run.job_protocol import JobChannel, JobTokenIssuer
from agentclaw.community.core.evolution_run.job_service import JobService
from agentclaw.community.core.evolution_run.store import EvolutionStore

DEFAULT_DB_PATH = ":memory:"


class EvolutionJobsModule(Module):
    """把进化作业面接进平台：唯一需要外部配置的是 SQLite 路径。"""

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

        return JobTokenIssuer(secret=secrets.token_bytes(32))

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
    ) -> JobChannel:
        return JobChannel(store=store, jobs=jobs, genome=genome, tokens=tokens)
