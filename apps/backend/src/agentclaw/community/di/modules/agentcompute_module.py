from __future__ import annotations

import logging

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.api.agentcompute.service import AgentComputeService
from agentclaw.community.api.agentcompute.service_protocol import (
    AgentComputeServiceProtocol,
)
from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.orchestration.orchestrator import Orchestrator
from agentclaw.community.core.agentcompute.orchestration.termination import (
    GuardService, TerminationEvaluator,
)
from agentclaw.community.core.agentcompute.plugins.decision_parser.parser import (
    DecisionParser,
)
from agentclaw.community.core.agentcompute.plugins.llm.client import (
    StubLLMProvider, OpenAICompatibleClient,
)
from agentclaw.community.core.agentcompute.plugins.agents.factory import (
    DefaultSubagentFactory,
)
from agentclaw.community.core.agentcompute.plugins.artifact_store.filesystem import (
    FilesystemArtifactStore,
)
from agentclaw.community.core.agentcompute.repository.impl import (
    AgentRepository, GuardEventRepository, JobRepository, MessageRepository,
    ResultShardRepository, RoundRepository, RunRepository,
)
from agentclaw.community.core.agentcompute.runtime.subagent_runtime import (
    SubagentRuntime,
)
from agentclaw.community.core.agentcompute.runtime.swarm_run import SwarmRunHandler
from agentclaw.community.core.agentcompute.runtime.worker import AgentComputeWorker
from agentclaw.community.core.agentcompute.spi.llm_provider import LLMProvider
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = logging.getLogger(__name__)


class AgentComputeModule(Module):

    def configure(self, binder: Binder) -> None:
        binder.bind(AgentComputeConfig, scope=singleton)
        binder.bind(RunRepository, scope=singleton)
        binder.bind(AgentRepository, scope=singleton)
        binder.bind(MessageRepository, scope=singleton)
        binder.bind(ResultShardRepository, scope=singleton)
        binder.bind(RoundRepository, scope=singleton)
        binder.bind(GuardEventRepository, scope=singleton)
        binder.bind(JobRepository, scope=singleton)
        binder.bind(DecisionParser, scope=singleton)
        binder.bind(GuardService, scope=singleton)
        binder.bind(Orchestrator, scope=singleton)
        binder.bind(SubagentRuntime, scope=singleton)
        binder.bind(SwarmRunHandler, scope=singleton)
        binder.bind(AgentComputeWorker, scope=singleton)
        binder.bind(AgentComputeServiceProtocol, to=AgentComputeService, scope=singleton)

    @provider
    @singleton
    def provide_config(self) -> AgentComputeConfig:
        return AgentComputeConfig()

    @provider
    @singleton
    @inject
    def provide_llm(self, config: AgentComputeConfig) -> LLMProvider:
        if config.llm is not None and config.llm.base_url:
            return OpenAICompatibleClient(
                base_url=config.llm.base_url,
                api_key=config.llm.api_key,
                default_model=config.llm.default_model,
                orchestrator_model=config.llm.orchestrator_model,
                timeout_seconds=config.llm.timeout_seconds,
                max_tokens=config.llm.max_tokens,
                max_retries=config.llm.max_retries,
            )
        return StubLLMProvider()

    @provider
    @singleton
    @inject
    def provide_run_repo(self, db: DatabasePlugin) -> RunRepository:
        return RunRepository(db)

    @provider
    @singleton
    @inject
    def provide_agent_repo(self, db: DatabasePlugin) -> AgentRepository:
        return AgentRepository(db)

    @provider
    @singleton
    @inject
    def provide_message_repo(self, db: DatabasePlugin) -> MessageRepository:
        return MessageRepository(db)

    @provider
    @singleton
    @inject
    def provide_shard_repo(self, db: DatabasePlugin) -> ResultShardRepository:
        return ResultShardRepository(db)

    @provider
    @singleton
    @inject
    def provide_round_repo(self, db: DatabasePlugin) -> RoundRepository:
        return RoundRepository(db)

    @provider
    @singleton
    @inject
    def provide_guard_repo(self, db: DatabasePlugin) -> GuardEventRepository:
        return GuardEventRepository(db)

    @provider
    @singleton
    @inject
    def provide_job_repo(self, db: DatabasePlugin) -> JobRepository:
        return JobRepository(db)

    @provider
    @singleton
    @inject
    def provide_orchestrator(
        self,
        llm: LLMProvider,
        parser: DecisionParser,
        guards: GuardService,
        config: AgentComputeConfig,
    ) -> Orchestrator:
        return Orchestrator(llm=llm, parser=parser, guards=guards, config=config)

    @provider
    @singleton
    @inject
    def provide_runtime(
        self,
        llm: LLMProvider,
        config: AgentComputeConfig,
    ) -> SubagentRuntime:
        factory = DefaultSubagentFactory(llm)
        store = FilesystemArtifactStore(config.artifact_store_root)
        return SubagentRuntime(
            factory=factory,
            artifact_store=store,
            max_width=config.orchestration.max_width,
            max_attempts=config.orchestration.subagent_max_attempts,
            per_task_timeout=config.orchestration.per_subagent_timeout,
            artifact_inline_threshold=config.artifact_inline_threshold,
        )

    @provider
    @singleton
    @inject
    def provide_handler(
        self,
        config: AgentComputeConfig,
        orchestrator: Orchestrator,
        runtime: SubagentRuntime,
        run_repo: RunRepository,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        shard_repo: ResultShardRepository,
        round_repo: RoundRepository,
        guard_repo: GuardEventRepository,
        job_repo: JobRepository,
    ) -> SwarmRunHandler:
        from agentclaw.community.core.agentcompute.orchestration.termination import (
            TerminationEvaluator,
        )
        termination = TerminationEvaluator(
            max_turns=config.orchestration.max_turns,
            max_total_agents=config.orchestration.max_total_agents,
        )
        return SwarmRunHandler(
            config=config,
            orchestrator=orchestrator,
            runtime=runtime,
            run_repo=run_repo,
            agent_repo=agent_repo,
            message_repo=message_repo,
            shard_repo=shard_repo,
            round_repo=round_repo,
            guard_repo=guard_repo,
            job_repo=job_repo,
            termination=termination,
            guards=GuardService(
                max_width=config.orchestration.max_width,
                max_depth=config.orchestration.max_depth,
            ),
        )

    @provider
    @singleton
    @inject
    def provide_worker(
        self,
        config: AgentComputeConfig,
        handler: SwarmRunHandler,
        job_repo: JobRepository,
    ) -> AgentComputeWorker:
        return AgentComputeWorker(
            config=config, handler=handler, job_repo=job_repo,
        )

    @provider
    @singleton
    @inject
    def provide_service(
        self,
        run_repo: RunRepository,
        job_repo: JobRepository,
        config: AgentComputeConfig,
    ) -> AgentComputeService:
        return AgentComputeService(
            run_repo=run_repo,
            job_repo=job_repo,
            deadline_seconds=int_or(config.orchestration.slice_max_seconds, 3600),
        )


def int_or(val: float, default: int) -> int:
    return int(val) if val else default