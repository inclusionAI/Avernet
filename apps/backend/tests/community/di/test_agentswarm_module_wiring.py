"""AgentSwarmModule provider wiring — the PR-2655 review fixes.

Pinned here (each maps to a review finding):

- P0-1 — ``provide_llm`` resolves the llm block's ``secret_name``
  through the ``SecretResolver`` seam (exactly like the harness LLM)
  and hands the *resolved value* to the client; the name travels only
  as the lookup key, and an unresolvable secret fails clear with
  ``AgentSwarmConfigError`` instead of shipping the name as a Bearer.
- P0-9 — ``bot_platform`` binds the host adapter with no profile-level
  identity: the provisioning owner exists only per-run/per-call (the
  authenticated submitter the run carries), every seam method passes
  the run's ``owner_id`` through, and the platform itself refuses a
  blank one. ``provide_runtime`` still degrades a failed platform
  construction to none-platform mode instead of wedging boot.
- P1-9 — the config-built ``GuardService`` is one shared singleton
  (orchestrator and handler enforce identical limits from the yaml
  block), ``SubagentRuntime`` receives an ``UpstreamBreaker`` built
  from ``config.upstream_breaker`` (the block is no longer dead), and
  ``TerminationEvaluator`` gets the configured wall-clock /
  no-progress values instead of hardcoded defaults.

Provider methods are called directly with fakes (the established DI
test pattern — see ``test_harness_llm_provider.py``); the one test
that needs the real platform chain rides the ``test_injector``
fixture.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, AgentSwarmExecutorConfig, AgentSwarmOrchestrationConfig,
    AgentSwarmUpstreamBreakerConfig, LlmConfig,
)
from agentclaw.community.core.agentswarm.orchestration.decision_parser import (
    DecisionParser,
)
from agentclaw.community.core.agentswarm.runtime.breaker import (
    BreakerState, UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.service.protocols import (
    AgentSwarmConfigError,
)
from agentclaw.community.di.modules.agentswarm import AgentSwarmModule
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform
from agentclaw.community.plugins.local.agentswarm.llm_openai import (
    OpenAICompatibleClient,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider


# ── fakes ──────────────────────────────────────────────────────────────

@dataclass
class _Secret:
    secret_user: str = "agent-user"
    secret_value: str = "resolved-llm-token"


class _RecordingResolver:
    """Fake SecretResolver — records names, serves one canned secret."""

    def __init__(self, secret=_Secret(), error=None):
        object.__setattr__(self, "_secret", secret)
        object.__setattr__(self, "_error", error)
        self.seen: list[str] = []

    def get_secret(self, name):
        self.seen.append(name)
        if getattr(self, "_error") is not None:
            raise getattr(self, "_error")
        return getattr(self, "_secret")


class _RaisingInjector:
    """Stands in for ``injector.Injector`` in direct provider calls."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.requested: list[object] = []

    def get(self, key):
        self.requested.append(key)
        raise self._exc


class _PlatformBrokenInjector:
    """Platform chain broken, interior wiring healthy: raises for the
    ``BotPlatform`` binding only, serves inert stubs for every other key —

    the P1-9 tests use it to compose a runtime whose non-platform
    wiring (interior repos, the factory, the binding store) stays
    observable while only the host seam is absent, mirroring the
    production none-platform degrade."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.requested: list[object] = []

    def get(self, key):
        self.requested.append(key)
        if key is BotPlatform:
            raise self._exc
        return object()


class _ServingInjector:
    def __init__(self, result) -> None:
        self._result = result
        self.requested: list[object] = []

    def get(self, key):
        self.requested.append(key)
        return self._result


class _Platform:
    def create_bot(self, *, spec, idempotency_key):
        return "bot-1"

    def bot_status(self, bot_id):
        return "PROVISIONING"

    def delete_bot(self, bot_id):
        return True

    def chat(self, bot_id, message):
        return "ok"


_WIRING_LOGGER = "agentclaw.community.di.modules.agentswarm"


# ── P0-1: the LLM client receives the RESOLVED secret ──────────────────

def _llm_config(api_key: str = "LLM_AUTH_TOKEN") -> AgentSwarmConfig:
    return AgentSwarmConfig(
        llm=LlmConfig(base_url="https://llm.test/v1", api_key=api_key,
                      default_model="glm-5.2"),
    )


class TestLlmSecretResolution:

    def test_client_gets_the_resolved_value_not_the_secret_name(self):
        resolver = _RecordingResolver()
        llm = AgentSwarmModule().provide_llm(_llm_config(), resolver)
        assert isinstance(llm, OpenAICompatibleClient)
        assert resolver.seen == ["LLM_AUTH_TOKEN"]
        assert llm._api_key == "resolved-llm-token"
        assert llm._client.headers["Authorization"] == (
            "Bearer resolved-llm-token"
        )

    def test_unresolvable_secret_fails_clear(self):
        resolver = _RecordingResolver(secret=None)
        with pytest.raises(AgentSwarmConfigError) as excinfo:
            AgentSwarmModule().provide_llm(_llm_config(), resolver)
        assert "LLM_AUTH_TOKEN" in str(excinfo.value)

    def test_blank_secret_name_fails_clear(self):
        resolver = _RecordingResolver()
        with pytest.raises(AgentSwarmConfigError):
            AgentSwarmModule().provide_llm(_llm_config(api_key=""), resolver)
        assert resolver.seen == []

    def test_resolver_backend_error_is_wrapped_with_the_cause(self):
        resolver = _RecordingResolver(error=RuntimeError("backend down"))
        with pytest.raises(AgentSwarmConfigError) as excinfo:
            AgentSwarmModule().provide_llm(_llm_config(), resolver)
        assert isinstance(excinfo.value.__cause__, RuntimeError)

    def test_empty_secret_value_fails_clear(self):
        blank = _Secret(secret_value="")
        resolver = _RecordingResolver(secret=blank)
        with pytest.raises(AgentSwarmConfigError):
            AgentSwarmModule().provide_llm(_llm_config(), resolver)

    def test_unconfigured_llm_stays_the_stub(self):
        resolver = _RecordingResolver()
        llm = AgentSwarmModule().provide_llm(AgentSwarmConfig(), resolver)
        assert isinstance(llm, StubLLMProvider)
        assert resolver.seen == []


# ── P0-9: identity is per-call only; the provider needs no config ─────

class TestBotPlatformIdentity:

    def test_provider_builds_the_platform_without_any_host_block(
            self, monkeypatch,
    ):
        from agentclaw.community.plugins.community.agentswarm import (
            host as host_module,
        )
        from agentclaw.community.plugins.community.agentswarm.host import (
            AvernetBotPlatform,
        )
        seen: dict = {}
        monkeypatch.setattr(
            host_module, "set_chat_bridge_max_workers",
            lambda workers: seen.setdefault("workers", workers),
        )
        platform = AgentSwarmModule().bot_platform(
            injector=_ServingInjector(object()),
            config=AgentSwarmConfig(executor=AgentSwarmExecutorConfig(
                max_platform_workers=7)),
        )
        assert isinstance(platform, AvernetBotPlatform)
        assert not hasattr(platform, "_default_owner")
        assert seen["workers"] == 7

    def test_platform_is_built_with_no_ambient_identity(
            self, test_injector,
    ):
        from agentclaw.community.plugins.community.agentswarm.host import (
            AvernetBotPlatform,
        )
        test_injector.binder.bind(
            AgentSwarmConfig, to=AgentSwarmConfig())
        platform = test_injector.get(BotPlatform)
        assert isinstance(platform, AvernetBotPlatform)
        assert not hasattr(platform, "_default_owner")

    def test_runtime_degrades_a_config_error_in_the_platform_chain(
            self, caplog,
    ):
        refusal = AgentSwarmConfigError("platform chain misconfigured")
        injector = _PlatformBrokenInjector(refusal)
        config = AgentSwarmConfig()
        with caplog.at_level(logging.WARNING, logger=_WIRING_LOGGER):
            runtime = AgentSwarmModule().provide_runtime(
                injector=injector, llm=StubLLMProvider(), config=config,
            )
        assert injector.requested[0] is BotPlatform
        assert "platform chain misconfigured" in caplog.text
        assert runtime is not None

    def test_runtime_takes_the_bound_platform(self):
        platform = _Platform()
        injector = _ServingInjector(platform)
        config = AgentSwarmConfig()
        runtime = AgentSwarmModule().provide_runtime(
            injector=injector, llm=StubLLMProvider(), config=config,
        )
        assert injector.requested[0] is BotPlatform
        assert runtime._factory._platform is platform


# ── P1-9: config-driven guards / breaker / evaluator ────────────────────

def _wired_config() -> AgentSwarmConfig:
    return AgentSwarmConfig(
        orchestration=AgentSwarmOrchestrationConfig(
            max_turns=11, max_width=3, max_depth=2, respawn_max=1,
            wall_clock_seconds=1800.0, no_progress_threshold=6,
        ),
        upstream_breaker=AgentSwarmUpstreamBreakerConfig(
            failure_threshold=2, open_seconds=11.0,
        ),
    )


class TestConfiguredOrchestration:

    def test_orchestrator_and_handler_share_the_configured_guard(self):
        module = AgentSwarmModule()
        config = _wired_config()
        guards = module.provide_guards(config)
        orchestrator = module.provide_orchestrator(
            llm=StubLLMProvider(), parser=DecisionParser(),
            guards=guards, config=config,
        )
        runtime = module.provide_runtime(
            injector=_PlatformBrokenInjector(AgentSwarmConfigError("no platform")),
            llm=StubLLMProvider(), config=config,
        )
        handler = module.provide_handler(
            config=config, orchestrator=orchestrator, runtime=runtime,
            guards=guards,
            run_repo=object(), agent_repo=object(),
            message_repo=object(), result_repo=object(),
            round_repo=object(), guard_repo=object(), dispatch_repo=object(),
        )
        assert orchestrator._guards is guards
        assert handler._guards is guards
        assert guards._max_width == 3
        assert guards._max_depth == 2
        assert guards._respawn_max == 1
        assert guards._no_progress_threshold == 6

    def test_runtime_receives_a_breaker_built_from_config(self):
        config = _wired_config()
        runtime = AgentSwarmModule().provide_runtime(
            injector=_PlatformBrokenInjector(AgentSwarmConfigError("no platform")),
            llm=StubLLMProvider(), config=config,
        )
        breaker = runtime._upstream_breaker
        assert isinstance(breaker, UpstreamBreaker)
        assert breaker.state is BreakerState.CLOSED
        assert breaker._failure_threshold == 2
        assert breaker._open_seconds == 11.0

    def test_default_config_still_wires_a_breaker(self):
        runtime = AgentSwarmModule().provide_runtime(
            injector=_PlatformBrokenInjector(AgentSwarmConfigError("no platform")),
            llm=StubLLMProvider(), config=AgentSwarmConfig(),
        )
        assert runtime._upstream_breaker is not None

    def test_termination_evaluator_receives_the_config_values(self):
        module = AgentSwarmModule()
        config = _wired_config()
        orchestrator = module.provide_orchestrator(
            llm=StubLLMProvider(), parser=DecisionParser(),
            guards=module.provide_guards(config), config=config,
        )
        runtime = module.provide_runtime(
            injector=_PlatformBrokenInjector(AgentSwarmConfigError("no platform")),
            llm=StubLLMProvider(), config=config,
        )
        handler = module.provide_handler(
            config=config, orchestrator=orchestrator, runtime=runtime,
            guards=module.provide_guards(config),
            run_repo=object(), agent_repo=object(),
            message_repo=object(), result_repo=object(),
            round_repo=object(), guard_repo=object(), dispatch_repo=object(),
        )
        evaluator = handler._termination
        assert evaluator._max_turns == 11
        assert evaluator._max_total_agents == (
            config.orchestration.max_total_agents
        )
        assert evaluator._wall_clock_seconds == 1800.0
        assert evaluator._no_progress_threshold == 6
