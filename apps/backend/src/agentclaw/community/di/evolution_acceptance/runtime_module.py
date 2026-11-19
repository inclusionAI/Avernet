"""Legacy acceptance-only Injector assembly; never part of Backend startup.

Environment knobs and permissive fallback ports are retained solely for explicit
legacy integration tests. Production uses apps/evolution composition and settings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from injector import (
    Injector,
    Module,
    inject,
    provider,
    singleton,
)

from avernet_evolution.core.evolution_ledger import LocalExperimentLedger
from avernet_evolution.core.evolution_run.bindings_service import BindingsService
from avernet_evolution.core.evolution_run.budget import Budget
from avernet_evolution.core.evolution_run.budget_service import BudgetService
from avernet_evolution.core.evolution_run.container_host import ContainerHost
from agentclaw.community.plugins.evolution.genome_registry import RegistryGenomePort
from avernet_evolution.core.evolution_run.job_protocol import JobTokenIssuer
from avernet_evolution.core.evolution_run.job_service import JobService
from avernet_evolution.core.evolution_run.operation_service import OperationService
from avernet_evolution.core.evolution_run.ports import (
    BotPolicy,
    GovernancePort,
    KillSwitchesPort,
    StrategyRegistryPort,
)
from avernet_evolution.core.evolution_run.run_service import RunService
from avernet_evolution.core.evolution_run.runner import EvolutionRunner
from avernet_evolution.core.evolution_run.scheduler import TriggerScheduler
from avernet_evolution.core.evolution_run.store import EvolutionStore
from avernet_evolution.core.evolution_strategy.registry import InMemoryStrategyRegistry
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.sandbox_runtime import SandboxRuntimeClient

__all__ = ["EvolutionRuntimeModule"]

logger = get_logger()

DEFAULT_ENDPOINT = "http://127.0.0.1:8000/openapi/v1/evolution/jobs"


class _NoSandbox:
    """未配置镜像时的沙箱端口：创建即失败，由宿主记进 ``last_error``。"""

    async def create(self, spec: Any) -> str:
        raise RuntimeError("EVOLUTION_WORKER_IMAGE is not configured")

    async def exec(self, sandbox_id: str, command: str, *, timeout_ms: int) -> int:
        raise RuntimeError("EVOLUTION_WORKER_IMAGE is not configured")

    async def destroy(self, sandbox_id: str) -> None:
        return None


class _IdleScheduler:
    """消费关闭时的调度器替身：让 runner 可以被构造，但永不触发。"""

    async def tick(self, now: Any = None) -> list[Any]:
        return []


class _IdleHost:
    """消费关闭时的宿主替身：永远没有可派发的作业。"""

    async def run_once(self, worker_id: str) -> bool:
        return False


class _PermissiveKills:
    """没有绑定停止开关时的默认：什么都不停。"""

    async def strategy_disabled(self, strategy: str, version: str) -> bool:
        return False

    async def bot_frozen(self, bot: Any) -> bool:
        return False

    async def paused(self) -> bool:
        return False


class _PassThroughGovernance:
    """没有绑定治理时的默认：引擎取 bot 自身字段，预算上限尽力而为。"""

    def __init__(self) -> None:
        self._ceiling = Budget(max_usd=float("inf"), max_wall_clock_s=10**9)

    async def engine(self, bot: Any) -> str:
        return getattr(bot, "engine_type", "") or "openclaw"

    async def policy(self, bot: Any) -> BotPolicy:
        return BotPolicy()

    async def bot_ceiling(self, bot: Any) -> Any:
        return self._ceiling

    async def tenant_ceiling(self, bot: Any) -> Any:
        return self._ceiling

    async def profile_problems(self, profile: str) -> list[str]:
        return []


@dataclass(frozen=True)
class _Services:
    """一次装配里共用的那些服务；避免为调度器和宿主各建一份。"""

    store: EvolutionStore
    jobs: JobService
    tokens: JobTokenIssuer
    genome: RegistryGenomePort
    registry: Any
    kills: Any
    runs: RunService
    bindings: BindingsService
    budget: BudgetService
    operations: OperationService
    ledger: LocalExperimentLedger


def _enabled() -> bool:
    return os.environ.get("EVOLUTION_CONSUMER_ENABLED", "0") == "1"


def _strategy_refs() -> tuple[str, ...]:
    raw = os.environ.get("EVOLUTION_STRATEGY_REFS", "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _image_of() -> Callable[[Any], str]:
    image = os.environ.get("EVOLUTION_WORKER_IMAGE", "").strip()
    return lambda _run: image


def _resolve(injector: Injector, key: Any, fallback: Any) -> Any:
    """取已绑定的端口，未绑定就用默认实现。"""

    current = injector
    while current is not None:
        if current.binder.has_explicit_binding_for(key):
            # A configured provider failing is not a missing provider.
            return injector.get(key)
        current = current.parent
    return fallback


class EvolutionRuntimeModule(Module):
    """把「调度 + 沙箱派发」接进生命周期；默认不启动后台消费。"""

    @singleton
    @provider
    @inject
    def evolution_runner(self, injector: Injector) -> EvolutionRunner:
        if not _enabled():
            return EvolutionRunner(
                host=_IdleHost(), scheduler=_IdleScheduler(), enabled=False
            )
        services = injector.get(_Services)
        return EvolutionRunner(
            host=self._container_host(injector, services),
            scheduler=TriggerScheduler(
                store=services.store,
                runs=services.runs,
                genome=services.genome,
                kills=services.kills,
            ),
            enabled=True,
            jobs=services.jobs,
            poll_interval_s=float(os.environ.get("EVOLUTION_POLL_INTERVAL_S", "15")),
            max_jobs_per_tick=int(os.environ.get("EVOLUTION_MAX_JOBS_PER_TICK", "1")),
        )

    @singleton
    @provider
    @inject
    def _services(self, injector: Injector) -> _Services:
        store = injector.get(EvolutionStore)
        registry = _resolve(injector, StrategyRegistryPort, InMemoryStrategyRegistry())
        governance = _resolve(injector, GovernancePort, _PassThroughGovernance())
        kills = _resolve(injector, KillSwitchesPort, _PermissiveKills())
        genome = injector.get(RegistryGenomePort)
        budget = BudgetService(store, governance=governance)
        bindings = BindingsService(
            store,
            registry=registry,
            genome=genome,
            governance=governance,
            kills=kills,
        )
        ledger = injector.get(LocalExperimentLedger)
        runs = RunService(
            store,
            bindings=bindings,
            registry=registry,
            genome=genome,
            budget=budget,
            kills=kills,
            ledger=ledger,
        )
        return _Services(
            store=store,
            jobs=injector.get(JobService),
            tokens=injector.get(JobTokenIssuer),
            genome=genome,
            registry=registry,
            kills=kills,
            runs=runs,
            bindings=bindings,
            budget=budget,
            operations=OperationService(store, budget=budget),
            ledger=ledger,
        )

    @singleton
    @provider
    @inject
    def run_service(self, services: _Services) -> RunService:
        return services.runs

    @singleton
    @provider
    @inject
    def bindings_service(self, services: _Services) -> BindingsService:
        return services.bindings

    @singleton
    @provider
    @inject
    def budget_service(self, services: _Services) -> BudgetService:
        return services.budget

    @singleton
    @provider
    @inject
    def operation_service(self, services: _Services) -> OperationService:
        return services.operations

    @singleton
    @provider
    def experiment_ledger(self) -> LocalExperimentLedger:
        database = os.environ.get("EVOLUTION_DB_PATH", ":memory:")
        return LocalExperimentLedger(
            ":memory:" if database == ":memory:" else f"{database}.ledger"
        )

    def _container_host(self, injector: Injector, services: _Services) -> ContainerHost:
        sandbox_runtime = _resolve(injector, SandboxRuntimeClient, None)
        if sandbox_runtime is None:
            sandbox: Any = _NoSandbox()
        else:
            from agentclaw.community.plugins.evolution.sandbox_worker import (
                SandboxRuntimeJobWorker,
            )

            sandbox = SandboxRuntimeJobWorker(
                runtime=sandbox_runtime,
                template_id=os.environ.get("EVOLUTION_WORKER_TEMPLATE_ID", ""),
            )
        return ContainerHost(
            sandbox=sandbox,
            store=services.store,
            jobs=services.jobs,
            runs=services.runs,
            image_of=_image_of(),
            job_endpoint=os.environ.get("EVOLUTION_JOB_ENDPOINT", DEFAULT_ENDPOINT),
            strategy_refs=_strategy_refs(),
            tokens=services.tokens,
        )
