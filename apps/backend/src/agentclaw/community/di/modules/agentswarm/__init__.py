from __future__ import annotations

import logging

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.agentswarm_service import (
    AgentSwarmServiceProtocol,
)

from agentclaw.community.core.agentswarm.config.settings import AgentSwarmConfig
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.orchestration.orchestrator import Orchestrator
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider
from agentclaw.community.plugins.local.agentswarm.llm_openai import OpenAICompatibleClient
from agentclaw.community.plugins.local.agentswarm.factory import (
    DefaultSubagentFactory,
)
from agentclaw.community.di.modules.config_module import _block
from agentclaw.community.core.agentswarm.repository.audit.impl import (
    AgentSwarmAuditWriter,
)
from agentclaw.community.plugins.local.agentswarm.artifacts import (
    FilesystemArtifactStore,
)
from agentclaw.community.core.agentswarm.repository.agent import AgentRepository
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.job import JobRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.shard import ResultShardRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentswarm.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform
from agentclaw.community.plugin_api.agentswarm.llm import LLMProvider
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = logging.getLogger(__name__)


class AgentSwarmModule(Module):

    def configure(self, binder: Binder) -> None:
        binder.bind(DecisionParser, scope=singleton)
        binder.bind(GuardService, scope=singleton)

    @singleton
    @provider
    @inject
    def agent_swarm_service(
        self,
        run_repo: RunRepository,
        job_repo: JobRepository,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        round_repo: RoundRepository,
        guard_repo: GuardEventRepository,
        shard_repo: ResultShardRepository,
        config: AgentSwarmConfig,
    ) -> AgentSwarmService:
        return AgentSwarmService(
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
    def agent_swarm_service_protocol(
        self,
        service: AgentSwarmService,
    ) -> AgentSwarmServiceProtocol:
        return service

    @provider
    @singleton
    def provide_config(self) -> AgentSwarmConfig:
        # The `agentswarm` block carries the operational knobs; the LLM
        # comes from the TOP-LEVEL `llm` yaml block (the same one the
        # harness reads — base_url + secret_name, resolved via
        # SecretResolver). No agentswarm-specific LLM config exists.
        block = _block("agentswarm")
        block["llm"] = _block("llm")
        return AgentSwarmConfig.from_block(block)

    @provider
    @singleton
    @inject
    def provide_llm(
        self,
        config: AgentSwarmConfig,
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
                egress=config.egress,
            )
        return StubLLMProvider()

    @provider
    @singleton
    @inject
    def provide_audit_writer(self, db: DatabasePlugin) -> AgentSwarmAuditWriter:
        return AgentSwarmAuditWriter(db)

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
        config: AgentSwarmConfig,
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
        from agentclaw.community.plugins.community.agentswarm.host import (
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
        config: AgentSwarmConfig,
    ) -> SubagentRuntime:
        # The avernet BotPlatform is a host binding, not an agentswarm one:
        # profiles that run the platform side bind BotPlatform (the
        # in-process seam); others resolve no platform and avernet-kind
        # spawns fail clear at the factory.
        try:
            platform: BotPlatform | None = injector.get(BotPlatform)
        except Exception:
            platform = None
        factory = DefaultSubagentFactory(llm, platform)
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
        config: AgentSwarmConfig,
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
        from agentclaw.community.core.agentswarm.orchestration.termination import (
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
        config: AgentSwarmConfig,
        handler: SwarmRunHandler,
        job_repo: JobRepository,
    ) -> AgentSwarmWorker:
        return AgentSwarmWorker(
            config=config, handler=handler, job_repo=job_repo,
        )