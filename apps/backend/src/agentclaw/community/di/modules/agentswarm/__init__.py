from __future__ import annotations

from agentclaw.community.log import get_logger

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.api.agentswarm_service import (
    AgentSwarmServiceProtocol,
)

from agentclaw.community.core.agentswarm.config.settings import AgentSwarmConfig
from agentclaw.community.core.agentswarm.service import AgentSwarmService
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError,
)
from agentclaw.community.core.agentswarm.orchestration.orchestrator import Orchestrator
from agentclaw.community.core.agentswarm.orchestration.guards import GuardService
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    UpstreamBreaker,
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
from agentclaw.community.core.agentswarm.repository.agent_attempt import (
    AgentAttemptRepository,
)
from agentclaw.community.core.agentswarm.repository.guard import GuardEventRepository
from agentclaw.community.core.agentswarm.repository.run_dispatch import RunDispatchRepository
from agentclaw.community.core.agentswarm.repository.message import MessageRepository
from agentclaw.community.core.agentswarm.repository.agent_result import AgentResultRepository
from agentclaw.community.core.agentswarm.repository.round import RoundRepository
from agentclaw.community.core.agentswarm.repository.run import RunRepository
from agentclaw.community.core.agentswarm.runtime.pool import (
    SubagentRuntime,
)
from agentclaw.community.core.agentswarm.runtime.handler import SwarmRunHandler
from agentclaw.community.core.agentswarm.runtime.worker import AgentSwarmWorker
from agentclaw.community.core.agentswarm.runtime.store import SliceStore
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform
from agentclaw.community.plugin_api.agentswarm.llm import LLMProvider
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.secret_resolver import SecretResolver

logger = get_logger("agentswarm")


def _resolve_llm_api_key(secret_name: str, resolver: SecretResolver) -> str:
    """Resolve the llm block's secret NAME to its credential value.

    The yaml ``llm.secret_name`` (carried on ``LlmConfig.api_key``) is a
    registry key, never the token itself — resolution goes through the
    injected ``SecretResolver`` exactly like the harness LLM
    (``core/harness/services/llm.py``): lookup by name, read
    ``.secret_value``. An empty name, a missing secret, or a failing
    backend fails clear with ``AgentSwarmConfigError`` (nothing is sent
    as a Bearer, and no value is stored on any config object).
    """
    if not secret_name:
        raise AgentSwarmConfigError(
            "agentswarm llm: base_url is set but secret_name is empty — "
            "no credential to resolve; set llm.secret_name and provision "
            "the secret"
        )
    try:
        secret = resolver.get_secret(secret_name)
    except Exception as exc:
        raise AgentSwarmConfigError(
            f"agentswarm llm: secret lookup for {secret_name!r} failed "
            f"({type(exc).__name__}: {exc}) — provision the secret or fix "
            "the secret backend"
        ) from exc
    value = getattr(secret, "secret_value", None)
    if value is None or not str(value).strip():
        raise AgentSwarmConfigError(
            f"agentswarm llm: secret {secret_name!r} resolved to no value "
            "— the swarm refuses to send an unauthenticated request"
        )
    return str(value)


class AgentSwarmModule(Module):

    def configure(self, binder: Binder) -> None:
        binder.bind(DecisionParser, scope=singleton)

    @singleton
    @provider
    @inject
    def agent_swarm_service(
        self,
        run_repo: RunRepository,
        dispatch_repo: RunDispatchRepository,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        round_repo: RoundRepository,
        guard_repo: GuardEventRepository,
        result_repo: AgentResultRepository,
        attempt_repo: AgentAttemptRepository,
        config: AgentSwarmConfig,
        injector: Injector,
    ) -> AgentSwarmService:
        return AgentSwarmService(
            run_repo=run_repo,
            dispatch_repo=dispatch_repo,
            agent_repo=agent_repo,
            message_repo=message_repo,
            round_repo=round_repo,
            guard_repo=guard_repo,
            result_repo=result_repo,
            attempt_repo=attempt_repo,
            config=config,
            authorization_source=self._authorization_source(injector),
        )

    @staticmethod
    def _authorization_source(injector: Injector):
        """The run surfaces' read-only tower into the pending passport gate.

        Same fail-clear shape as ``bot_platform`` below: profiles that can
        resolve the manifest seam and the passport plugin get the
        human-gate descriptor join on run detail and SSE — presence keyed
        off the facade's live PENDING status; profiles that cannot keep
        the pre-change reads (parked agents surface their ``bot_state``
        with ``None`` gate fields, and nothing else changes).
        """
        try:
            from agentclaw.community.core.bot_management.manifest_seam import (
                ManifestCreationSeam,
            )
            from agentclaw.community.plugin_api.passport import PassportPlugin
            from agentclaw.community.plugins.community.agentswarm.bot_authorization import (
                ManifestBotAuthorizationSource,
            )
            return ManifestBotAuthorizationSource(
                manifest_seam=injector.get(ManifestCreationSeam),
                passport_plugin=injector.get(PassportPlugin),
            )
        except Exception:
            logger.warning(
                "[agentswarm] authorization source resolution failed — run "
                "surfaces degrade to pre-change reads (parked agents surface "
                "no consent URLs)",
            )
            return None

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
        # The `agentswarm` block carries the operational knobs and its own LLM
        # sub-block (`agentswarm.llm` — base_url + secret_name, resolved via
        # SecretResolver, model via the run-level API parameter). The
        # top-level `llm` block belongs to the harness and is not read here.
        block = _block("agentswarm")
        return AgentSwarmConfig.from_block(block)

    @provider
    @singleton
    @inject
    def provide_guards(self, config: AgentSwarmConfig) -> GuardService:
        # One config-built singleton: the orchestrator and the handler
        # share this instance, so yaml guard limits are enforced
        # identically on both decision paths (P1-9 — a second
        # default-constructed GuardService let the orchestrator police
        # default limits the handler never used).
        return GuardService(
            max_width=config.orchestration.max_width,
            max_depth=config.orchestration.max_depth,
            respawn_max=config.orchestration.respawn_max,
            no_progress_threshold=config.orchestration.no_progress_threshold,
        )

    @provider
    @singleton
    @inject
    def provide_llm(
        self,
        config: AgentSwarmConfig,
        secret_resolver: SecretResolver,
    ) -> LLMProvider:
        if config.llm is not None and config.llm.base_url:
            resolved = _resolve_llm_api_key(config.llm.api_key, secret_resolver)
            return OpenAICompatibleClient(
                base_url=config.llm.base_url,
                api_key=resolved,
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
    def provide_result_repo(self, db: DatabasePlugin) -> AgentResultRepository:
        return AgentResultRepository(db)

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
    def provide_dispatch_repo(self, db: DatabasePlugin) -> RunDispatchRepository:
        return RunDispatchRepository(db)

    @provider
    @singleton
    @inject
    def provide_attempt_repo(self, db: DatabasePlugin) -> AgentAttemptRepository:
        return AgentAttemptRepository(db)

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
    def bot_platform(
        self,
        injector: Injector,
        config: AgentSwarmConfig,
    ) -> BotPlatform:
        """In-process host adapter. Heavy imports are call-local: the
        bot_management chain is circular at module import time
        (bot_service→aicoding→bot_service), deferring the chain here
        preserves the module's clean import.

        The provisioning owner is per-run and per-call exclusively — the
        authenticated submitter the run carries: the factory's P0-9 gate
        refuses a callerless run, and every seam call hands the run's
        owner_id to the platform, which itself refuses a blank one. No
        profile-level identity exists: the config names no owner, and
        construction never refuses for identity reasons.
        ``max_platform_workers`` sizes the module-scoped chat bridge
        (``executor`` block)."""
        from agentclaw.community.api.apply_run_service import (
            ApplyRunServiceProtocol,
        )
        from agentclaw.community.api.bot_service import BotServiceProtocol
        from agentclaw.community.core.bot_inventory.protocols import (
            BusinessSpaceContextProtocol,
        )
        from agentclaw.community.core.bot_management.manifest_seam import ManifestCreationSeam
        from agentclaw.community.core.repository.protocols.bot import BotRepository
        from agentclaw.community.core.service_bot.services.baas_service import BaasService
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
            apply_service=injector.get(ApplyRunServiceProtocol),
            space_context=injector.get(BusinessSpaceContextProtocol),
            baas_service=injector.get(BaasService),
            max_platform_workers=config.executor.max_platform_workers,
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
        # The bot BotPlatform is a host binding, not an agentswarm one:
        # profiles that run the platform side bind BotPlatform (the
        # in-process seam); others resolve no platform and bot-kind
        # spawns fail clear at the factory.
        try:
            platform: BotPlatform | None = injector.get(BotPlatform)
        except AgentSwarmConfigError as exc:
            # A config-family error raised inside the platform's
            # construction chain (a mis-parsed knob deep in a
            # platform-side dependency): precise, logged, and degraded
            # to the same fail-clear mode; bot-kind spawns fail
            # clear at the factory.
            logger.warning(
                "[agentswarm] BotPlatform construction failed: %s "
                "(bot-kind spawns will fail clear at the factory)", exc,
            )
            platform = None
        except Exception:
            # Anything else IS a wiring bug (unbound/misbound dependency in
            # the platform chain): logged with the traceback so the
            # degrade is observable, then the same fail-clear mode.
            logger.exception(
                "[agentswarm] BotPlatform resolution failed — degrading to "
                "none-platform mode; bot-kind spawns fail clear at the "
                "factory"
            )
            platform = None
        attempt_repo = injector.get(AgentAttemptRepository)
        factory = DefaultSubagentFactory(
            llm, platform, attempt_repo=attempt_repo,
            setup_timeout=config.orchestration.setup_timeout,
        )
        store = FilesystemArtifactStore(config.artifact_store_root)
        # The pool's binding-journal SliceStore (D-L): the same repos, its
        # own probe — the journal write's authority is the D28 claim fence
        # evaluated inside the insert, and this store never touches the
        # message/result plane the handler's own store drives.
        binding_store = SliceStore(
            config,
            injector.get(AgentRepository),
            injector.get(MessageRepository),
            injector.get(AgentResultRepository),
            injector.get(RoundRepository),
            lost_probe=lambda: False,
            attempt_repo=attempt_repo,
        )
        return SubagentRuntime(
            factory=factory,
            artifact_store=store,
            max_width=config.orchestration.max_width,
            max_attempts=config.orchestration.agent_max_attempts,
            per_task_timeout=config.orchestration.per_attempt_timeout,
            setup_timeout=config.orchestration.setup_timeout,
            artifact_inline_threshold=config.artifact_inline_threshold,
            upstream_breaker=UpstreamBreaker(
                failure_threshold=config.upstream_breaker.failure_threshold,
                open_seconds=config.upstream_breaker.open_seconds,
            ),
            binding_store=binding_store,
        )

    @provider
    @singleton
    @inject
    def provide_handler(
        self,
        config: AgentSwarmConfig,
        orchestrator: Orchestrator,
        runtime: SubagentRuntime,
        guards: GuardService,
        run_repo: RunRepository,
        agent_repo: AgentRepository,
        message_repo: MessageRepository,
        result_repo: AgentResultRepository,
        round_repo: RoundRepository,
        guard_repo: GuardEventRepository,
        dispatch_repo: RunDispatchRepository,
    ) -> SwarmRunHandler:
        from agentclaw.community.core.agentswarm.orchestration.termination import (
            TerminationEvaluator,
        )
        termination = TerminationEvaluator(
            max_turns=config.orchestration.max_turns,
            max_total_agents=config.orchestration.max_total_agents,
            wall_clock_seconds=config.orchestration.wall_clock_seconds,
            no_progress_threshold=config.orchestration.no_progress_threshold,
        )
        return SwarmRunHandler(
            config=config,
            orchestrator=orchestrator,
            runtime=runtime,
            run_repo=run_repo,
            agent_repo=agent_repo,
            message_repo=message_repo,
            result_repo=result_repo,
            round_repo=round_repo,
            guard_repo=guard_repo,
            dispatch_repo=dispatch_repo,
            termination=termination,
            guards=guards,
        )

    @provider
    @singleton
    @inject
    def provide_worker(
        self,
        config: AgentSwarmConfig,
        handler: SwarmRunHandler,
        dispatch_repo: RunDispatchRepository,
        run_repo: RunRepository,
        guard_repo: GuardEventRepository,
        attempt_repo: AgentAttemptRepository,
    ) -> AgentSwarmWorker:
        # run_repo/guard_repo host the maintenance reaper (async-run-and-
        # resume 4.1): the DI root always passes both, so production
        # workers sweep; constructor defaults keep embedded uses opt-out.
        # attempt_repo backs the terminal-cleanup reconciliation leg.
        return AgentSwarmWorker(
            config=config, handler=handler, dispatch_repo=dispatch_repo,
            run_repo=run_repo, guard_repo=guard_repo,
            attempt_repo=attempt_repo,
        )