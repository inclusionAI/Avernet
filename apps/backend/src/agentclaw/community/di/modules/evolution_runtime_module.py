"""进化运行时的生产装配（06 §10、§15）：让平台真正消费进化作业队列。

这条链有三段，缺一段平台就只“能收作业、不会跑作业”：

1. ``TriggerScheduler.tick()`` —— 把 cron 绑定变成排队的运行
2. ``ContainerHost.run_once()`` —— 把排队作业派发进一次性沙箱
3. ``EvolutionRunner`` —— 作为 ``Lifecycle`` 参与者按间隔驱动前两者

**后台消费默认关闭。** 只有显式设置 ``EVOLUTION_CONSUMER_ENABLED=1`` 才会起循环，
避免给已有部署与集成测试引入后台任务副作用。关闭时 runner 的两个生命周期钩子都是
无操作，平台仍可经 HTTP 手动提交运行、或人工驱动 ``host.run_once()``。

开启后需要的外部事实：

============================ ========================================== ==========================
环境变量                     含义                                       缺省
============================ ========================================== ==========================
EVOLUTION_CONSUMER_ENABLED   1 = 起后台消费循环                          关闭
EVOLUTION_WORKER_IMAGE       worker 镜像，必须 ``…@sha256:…`` 固定        无（派发会记配置错误）
EVOLUTION_STRATEGY_REFS      本进程负责派发的策略 ``ref`` 逗号列表       空（不派发任何作业）
EVOLUTION_JOB_ENDPOINT       worker 回连的作业协议端点                   本机 8000
EVOLUTION_POLL_INTERVAL_S    空闲轮询间隔（秒）                          15
EVOLUTION_MAX_JOBS_PER_TICK  单轮最多派发几个作业                        1
============================ ========================================== ==========================

三个端口（策略注册表 / 治理 / 停止开关）在「已绑定」时直接取用，未绑定时退化为
可运行的默认实现：空注册表（没注册策略就不触发）、只读直通治理（引擎取 bot 字段、
预算上限尽力而为）、无停止开关。这样本模块可以先接上，等 03/08 的生产实现落地后
无需改动接线。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from injector import Injector, Module, inject, provider, singleton

from agentclaw.community.api.bot_genome_registry_service import GenomeRegistryProtocol
from agentclaw.community.core.evolution_run.bindings_service import BindingsService
from agentclaw.community.core.evolution_run.budget import Budget
from agentclaw.community.core.evolution_run.budget_service import BudgetService
from agentclaw.community.core.evolution_run.container_host import ContainerHost
from agentclaw.community.core.evolution_run.genome_registry_port import RegistryGenomePort
from agentclaw.community.core.evolution_run.job_protocol import JobTokenIssuer
from agentclaw.community.core.evolution_run.job_service import JobService
from agentclaw.community.core.evolution_run.ports import (
    BotPolicy,
    GovernancePort,
    KillSwitchesPort,
    StrategyRegistryPort,
)
from agentclaw.community.core.evolution_run.run_service import RunService
from agentclaw.community.core.evolution_run.runner import EvolutionRunner
from agentclaw.community.core.evolution_run.scheduler import TriggerScheduler
from agentclaw.community.core.evolution_run.store import EvolutionStore
from agentclaw.community.core.evolution_strategy.registry import InMemoryStrategyRegistry
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
        self._ceiling = Budget(max_usd=float("inf"), max_wall_clock_s=10 ** 9)

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

    try:
        return injector.get(key)
    except Exception:  # noqa: BLE001 - 端口尚未实现时退化为默认
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
        services = self._services(injector)
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
        from agentclaw.community.core.evolution_ledger import LocalExperimentLedger

        database = os.environ.get("EVOLUTION_DB_PATH", ":memory:")
        ledger = LocalExperimentLedger(
            ":memory:" if database == ":memory:" else f"{database}.ledger"
        )
        runs = RunService(
            store,
            bindings=bindings,
            registry=registry,
            genome=injector.get(GenomeRegistryProtocol),
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
