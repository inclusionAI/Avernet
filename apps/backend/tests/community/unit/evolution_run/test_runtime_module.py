"""进化运行时装配（06 §10）：后台消费默认关闭，开启后接到容器宿主。"""

from __future__ import annotations

import pytest
from injector import Injector, Module

from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from avernet_evolution.core.evolution_run import (
    ContainerHost,
    EvolutionRunner,
    EvolutionStore,
    JobService,
    JobTokenIssuer,
)
from agentclaw.community.plugins.evolution.genome_registry import RegistryGenomePort
from agentclaw.community.di.evolution_acceptance.runtime_module import (
    EvolutionRuntimeModule,
    _image_of,
    _strategy_refs,
)
from agentclaw.community.kernel.lifecycle import discover_lifecycle_participants


class _FakeGenomeRegistry:
    """注册表替身：只满足装配期需要的形状。"""

    async def drift(self, bot):
        return None

    async def get_revision(self, bot, ref):
        raise KeyError(ref)

    async def read_content(self, bot, digest):
        return b""


class _StubModule(Module):
    """把进化作业面需要的外部事实绑成内存实现。"""

    def configure(self, binder) -> None:
        store = EvolutionStore(":memory:")
        registry = _FakeGenomeRegistry()
        binder.bind(EvolutionStore, to=store)
        binder.bind(JobService, to=JobService(store))
        binder.bind(JobTokenIssuer, to=JobTokenIssuer(secret=b"s"))
        binder.bind(GenomeRegistryProtocol, to=registry)
        binder.bind(RegistryGenomePort, to=RegistryGenomePort(registry=registry))


def test_consumer_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("EVOLUTION_CONSUMER_ENABLED", raising=False)
    runner = Injector([EvolutionRuntimeModule(), _StubModule()]).get(EvolutionRunner)

    assert runner.enabled is False
    assert not isinstance(runner.host, ContainerHost)


def test_enabled_consumer_dispatches_to_the_container_host(monkeypatch) -> None:
    monkeypatch.setenv("EVOLUTION_CONSUMER_ENABLED", "1")
    monkeypatch.setenv(
        "EVOLUTION_STRATEGY_REFS", "acme/x@1.0.0, acme/y@1.0.0"
    )
    monkeypatch.setenv("EVOLUTION_JOB_ENDPOINT", "http://platform.test/jobs")
    monkeypatch.setenv("EVOLUTION_POLL_INTERVAL_S", "7")
    monkeypatch.setenv("EVOLUTION_MAX_JOBS_PER_TICK", "3")

    runner = Injector([EvolutionRuntimeModule(), _StubModule()]).get(EvolutionRunner)

    assert runner.enabled is True
    assert isinstance(runner.host, ContainerHost)
    assert runner.host.strategy_refs == ("acme/x@1.0.0", "acme/y@1.0.0")
    assert runner.host.job_endpoint == "http://platform.test/jobs"
    assert runner.poll_interval_s == 7.0
    assert runner.max_jobs_per_tick == 3


@pytest.mark.asyncio
async def test_disabled_runner_lifecycle_is_a_noop(monkeypatch) -> None:
    monkeypatch.delenv("EVOLUTION_CONSUMER_ENABLED", raising=False)
    runner = Injector([EvolutionRuntimeModule(), _StubModule()]).get(EvolutionRunner)

    await runner.startup()
    assert runner._task is None
    await runner.shutdown()
    assert runner._task is None


def test_strategy_refs_parse_a_comma_list(monkeypatch) -> None:
    monkeypatch.setenv("EVOLUTION_STRATEGY_REFS", " a@1.0.0 , ,b@2.0.0 ")
    assert _strategy_refs() == ("a@1.0.0", "b@2.0.0")
    monkeypatch.setenv("EVOLUTION_STRATEGY_REFS", "")
    assert _strategy_refs() == ()


def test_image_resolver_uses_the_configured_digest(monkeypatch) -> None:
    image = "registry.test/evolution/worker@sha256:" + "a" * 64
    monkeypatch.setenv("EVOLUTION_WORKER_IMAGE", image)
    assert _image_of()(None) == image


def test_runner_is_discovered_as_a_lifecycle_participant(monkeypatch) -> None:
    """应用启动时按 ``Lifecycle`` 协议发现参与者；漏掉就是消费者静默不启动。"""

    monkeypatch.delenv("EVOLUTION_CONSUMER_ENABLED", raising=False)
    injector = Injector([EvolutionRuntimeModule(), _StubModule()])

    participants = discover_lifecycle_participants(injector)

    assert any(isinstance(p, EvolutionRunner) for p in participants)


@pytest.mark.asyncio
async def test_discovered_runner_hooks_are_safe(monkeypatch) -> None:
    """仿真器里 backend pod 启动会依次跑 bootstrap/startup/shutdown/teardown。"""

    monkeypatch.delenv("EVOLUTION_CONSUMER_ENABLED", raising=False)
    injector = Injector([EvolutionRuntimeModule(), _StubModule()])
    runner = next(
        p
        for p in discover_lifecycle_participants(injector)
        if isinstance(p, EvolutionRunner)
    )

    await runner.bootstrap()
    await runner.startup()
    assert runner._task is None          # 默认关闭：不起后台任务
    await runner.shutdown()
    await runner.teardown()
    assert runner.last_error is None


def test_explicit_provider_failure_is_not_hidden_by_fallback() -> None:
    from avernet_evolution.core.evolution_run.ports import StrategyRegistryPort
    from agentclaw.community.di.evolution_acceptance.runtime_module import _resolve

    injector = Injector()

    def broken_provider():
        raise RuntimeError("registry configuration failed")

    injector.binder.bind(StrategyRegistryPort, to=broken_provider)
    with pytest.raises(RuntimeError, match="registry configuration failed"):
        _resolve(injector, StrategyRegistryPort, object())


def test_optional_port_uses_explicit_parent_binding() -> None:
    from injector import InstanceProvider

    from avernet_evolution.core.evolution_run.ports import StrategyRegistryPort
    from agentclaw.community.di.evolution_acceptance.runtime_module import _resolve

    parent = Injector()
    registry = object()
    parent.binder.bind(StrategyRegistryPort, to=InstanceProvider(registry))
    child = parent.create_child_injector()
    assert _resolve(child, StrategyRegistryPort, None) is registry
