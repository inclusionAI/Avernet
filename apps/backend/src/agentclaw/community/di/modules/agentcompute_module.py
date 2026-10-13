from __future__ import annotations

import logging

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.agentcompute_service import (
    AgentComputeServiceProtocol,
)

from agentclaw.community.core.agentcompute.config.settings import AgentComputeConfig
from agentclaw.community.core.agentcompute.service import AgentComputeService
from agentclaw.community.core.agentcompute.orchestration.orchestrator import Orchestrator
from agentclaw.community.core.agentcompute.orchestration.guards import GuardService
from agentclaw.community.core.agentcompute.orchestration.termination import (
    TerminationEvaluator,
)
from agentclaw.community.core.agentcompute.plugins.decision_parser.parser import (
    DecisionParser,
)
from agentclaw.community.core.agentcompute.plugins.llm.stub import StubLLMProvider
from agentclaw.community.core.agentcompute.plugins.llm.openai import OpenAICompatibleClient
from agentclaw.community.core.agentcompute.plugins.agents.factory import (
    DefaultSubagentFactory,
)
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.core.agentcompute.plugins.artifact_store.filesystem import (
    FilesystemArtifactStore,
)
from agentclaw.community.core.agentcompute.repository.agent import AgentRepository
from agentclaw.community.core.agentcompute.repository.guard import GuardEventRepository
from agentclaw.community.core.agentcompute.repository.job import JobRepository
from agentclaw.community.core.agentcompute.repository.message import MessageRepository
from agentclaw.community.core.agentcompute.repository.shard import ResultShardRepository
from agentclaw.community.core.agentcompute.repository.round import RoundRepository
from agentclaw.community.core.agentcompute.repository.run import RunRepository
from agentclaw.community.core.agentcompute.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentcompute.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentcompute.runtime.worker import AgentComputeWorker
from agentclaw.community.core.agentcompute.runtime.observability import (
    AgentComputeObservability,
)
from agentclaw.community.core.agentcompute.spi.bot import BotPlatform
from agentclaw.community.core.agentcompute.spi.llm import LLMProvider
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.metrics import MetricsPlugin

logger = logging.getLogger(__name__)


class AgentComputeModule(Module):

    def configure(self, binder: Binder) -> None:
        binder.bind(DecisionParser, scope=singleton)
        binder.bind(GuardService, scope=singleton)
        # Metrics fallback for profiles whose infrastructure column does not
        # bind MetricsPlugin (today: the corp column, wired corp-side).
        # This module rides the base module list, which build_injector
        # installs BEFORE the profile column, so an explicit column
        # binding always replaces this default (last binding wins).
        from agentclaw.community.plugins.local.metrics import NoopMetrics

        binder.bind(MetricsPlugin, to=NoopMetrics, scope=singleton)

    @singleton
    @provider
    @inject
    def agent_compute_service(
        self,
        run_repo: RunRepository,
        job_repo: JobRepository,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        round_repo: RoundRepository,
        guard_repo: GuardEventRepository,
        shard_repo: ResultShardRepository,
        config: AgentComputeConfig,
    ) -> AgentComputeService:
        return AgentComputeService(
            run_repo=run_repo,
            job_repo=job_repo,
            agent_repo=agent_repo,
            message_repo=message_repo,
            round_repo=round_repo,
            guard_repo=guard_repo,
            shard_repo=shard_repo,
            config=config,
        )

    @singleton
    @provider
    @inject
    def agent_compute_service_protocol(
        self,
        service: AgentComputeService,
    ) -> AgentComputeServiceProtocol:
        return service

    @provider
    @singleton
    def provide_config(self) -> AgentComputeConfig:
        # The `agentcompute` block carries the operational knobs; the LLM
        # comes from the TOP-LEVEL `llm` yaml block (the same one the
        # harness reads — base_url + secret_name, resolved via
        # SecretResolver). No agentcompute-specific LLM config exists.
        block = _block("agentcompute")
        block["llm"] = _block("llm")
        return AgentComputeConfig.from_block(block)

    @provider
    @singleton
    @inject
    def provide_observability(
        self,
        metrics: MetricsPlugin,
    ) -> AgentComputeObservability:
        return AgentComputeObservability(metrics)

    @provider
    @singleton
    @inject
    def provide_llm(
        self,
        config: AgentComputeConfig,
        observability: AgentComputeObservability,
    ) -> LLMProvider:
        if config.llm is not None and config.llm.base_url:
            return OpenAICompatibleClient(
                base_url=config.llm.base_url,
                api_key=config.llm.api_key,
                default_model=config.llm.default_model,
                orchestrator_model=config.llm.orchestrator_model,
                timeout_seconds=config.llm.timeout_seconds,
                max_tokens=config.llm.max_tokens,
                max_retries=config.llm.max_retries,
                observability=observability,
                egress=config.egress,
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
    def bot_platform(self, injector: Injector) -> BotPlatform:
        """In-process host adapter from config.platform. Heavy imports
        are call-local: the bot_management chain is circular at module
        import time (bot_service→aicoding→bot_service), deferring the
        chain here preserves the module's clean import."""
        from agentclaw.community.api.bot_config_manifest_apply_service import (
            BotConfigManifestApplyServiceProtocol,
        )
        from agentclaw.community.api.bot_service import BotServiceProtocol
        from agentclaw.community.core.bot_inventory.protocols import (
            BusinessSpaceContextProtocol,
        )
        from agentclaw.community.core.bot_management.manifest_seam import ManifestCreationSeam
        from agentclaw.community.core.repository.protocols.bot import BotRepository
        from agentclaw.community.core.grt_chat.services import GrtChatService
        from agentclaw.community.core.skill_center.skill_set_service_factory_protocol import SkillSetServiceFactoryProtocol
        from agentclaw.community.plugin_api.passport import PassportPlugin
        from agentclaw.community.plugins.community.agentcompute_platform import (
            AvernetBotPlatform,
        )
        return AvernetBotPlatform(
            manifest_seam=injector.get(ManifestCreationSeam),
            bot_service=injector.get(BotServiceProtocol),
            passport_plugin=injector.get(PassportPlugin),
            skill_set_factory=injector.get(SkillSetServiceFactoryProtocol),
            bot_repo=injector.get(BotRepository),
            apply_service=injector.get(BotConfigManifestApplyServiceProtocol),
            space_context=injector.get(BusinessSpaceContextProtocol),
            grt_chat=injector.get(GrtChatService),
        )

    @provider
    @singleton
    @inject
    def provide_runtime(
        self,
        injector: Injector,
        llm: LLMProvider,
        config: AgentComputeConfig,
        observability: AgentComputeObservability,
    ) -> SubagentRuntime:
        # The avernet BotPlatform is a host binding, not an agentcompute one:
        # profiles that run the platform side bind BotPlatform (the
        # in-process seam); others resolve no platform and avernet-kind
        # spawns fail clear at the factory.
        try:
            platform: BotPlatform | None = injector.get(BotPlatform)
        except Exception:
            platform = None
        factory = DefaultSubagentFactory(llm, platform, observability)
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
        observability: AgentComputeObservability,
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
            observability=observability,
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