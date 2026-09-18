"""TaskPersistenceModule — binds the 5 task repository protocols to their ORM
implementations as singletons.

Profile-independent: the only per-profile difference is the ``DatabasePlugin``
injected into each constructor, which is bound one layer below by the profile's
infrastructure module (CommunityDatabase / SqliteDB / corp ZdasDB). Mirrors
``TaskQueueModule``.

The trajectory read-side ``TaskTrajectoryAssembler`` (REQ-8, P4) is bound here
too — it consumes ONLY ``TaskTrajectoryRepositoryProtocol`` (bound just above),
is a lightweight DI constructable service-layer object, and co-locating the
binding with the trajectory repo keeps the trajectory read-side wiring in one
place so P5's analysis service can ``Injected(...)`` it (per the P4 task's
"lean toward DI registration" guidance).

The trajectory analysis side ``TaskTrajectoryAnalyzer`` (REQ-9, P5a) is bound
here too — it consumes the bot-caller ``OpenApiBotPort`` (optional; resolved like
task_module resolves it, with try/except → None in lightweight DI injectors
that don't bind the port) + ``TrajectoryAnalysisConfig`` (from ConfigModule;
fall back to the in-process default when unbound). Co-located with the
assembler so the trajectory read + analysis wiring stays in one place.
"""

from injector import Binder, Injector, Module, provider, singleton

import logging

logger = logging.getLogger("task.persistence")

from agentclaw.community.core.repository.implementations.task.task_action_log_repository import (
    TaskActionLogRepository,
)
from agentclaw.community.core.repository.implementations.task.task_callback_correlation_repository import (
    TaskCallbackCorrelationRepository,
)
from agentclaw.community.core.repository.implementations.task.task_callback_repository import (
    TaskCallbackRepository,
)
from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_relation_repository import (
    TaskNodeRelationRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskActionLogRepositoryProtocol,
    TaskCallbackCorrelationRepositoryProtocol,
    TaskCallbackRepositoryProtocol,
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
    TaskTrajectoryRepositoryProtocol,
)
from agentclaw.community.core.task.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)
from agentclaw.community.core.task.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.api.task.task_trajectory_service import (
    TaskTrajectoryServiceProtocol,
)
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig


class TaskPersistenceModule(Module):
    """Bind the 5 task repository contracts to their unified ORM implementations."""

    def configure(self, binder: Binder) -> None:
        binder.bind(
            TaskActionLogRepositoryProtocol, to=TaskActionLogRepository, scope=singleton
        )
        binder.bind(
            TaskGraphRepositoryProtocol, to=TaskGraphRepository, scope=singleton
        )
        binder.bind(TaskInfoRepositoryProtocol, to=TaskInfoRepository, scope=singleton)
        binder.bind(TaskNodeRepositoryProtocol, to=TaskNodeRepository, scope=singleton)
        binder.bind(
            TaskNodeRunInfoRepositoryProtocol,
            to=TaskNodeRunInfoRepository,
            scope=singleton,
        )
        binder.bind(
            TaskNodeRelationRepositoryProtocol,
            to=TaskNodeRelationRepository,
            scope=singleton,
        )
        binder.bind(
            TaskCallbackRepositoryProtocol,
            to=TaskCallbackRepository,
            scope=singleton,
        )
        binder.bind(
            TaskTrajectoryRepositoryProtocol,
            to=TaskTrajectoryRepository,
            scope=singleton,
        )
        # Trajectory read-side assembler (REQ-8, P4): consumes the trajectory
        # repo (TaskTrajectoryRepositoryProtocol → TaskTrajectoryRepository
        # above); light DI-constructable service-layer object. Bound alongside
        # the trajectory repo so P5's analysis service can Injected(...) it.
        binder.bind(TaskTrajectoryAssembler, to=TaskTrajectoryAssembler, scope=singleton)
        binder.bind(
            TaskCallbackCorrelationRepositoryProtocol,
            to=TaskCallbackCorrelationRepository,
            scope=singleton,
        )

    @singleton
    @provider
    def task_trajectory_analyzer(
        self, injector: Injector
    ) -> TaskTrajectoryAnalyzer:
        """Construct the trajectory analysis multi-executor (REQ-9, P5a).

        Resolves its two optional dependencies the way ``TaskModule`` resolves
        ``OpenApiBotPort`` (try/except → None / default for lightweight DI
        injectors that don't bind the port / config):
        * ``OpenApiBotPort`` — the same bot-caller seam the planner / dispatcher
          uses; only corp/singlebox profiles bind it. Unbound → ``None`` → the
          ``tc_bot`` executor raises ``TrajectoryAnalysisError`` when invoked
          (the ``rule`` executor, being pure-function, still works without it).
        * ``TrajectoryAnalysisConfig`` — from ``ConfigModule`` (the deployment
          ``task_trajectory`` user_config block). Unbound → the analyzer's own
          ``_DefaultAnalysisConfig`` (180s timeout, ``analysis_bot_id=None``).

        Co-located with the assembler so trajectory read + analysis wiring lives
        in one module; lazy (only resolved when the P5b service asks for the
        analyzer, so the existing assembler-binding test is unaffected).
        """
        try:
            bot = injector.get(OpenApiBotPort)
        except Exception as exc:  # noqa: BLE001  unbound in lightweight DI injectors
            logger.info(
                "[task-persistence] OpenApiBotPort 未绑定 → 轨迹分析 tc_bot 执行者不可用:%s: %s",
                type(exc).__name__, exc,
            )
            bot = None
        try:
            config = injector.get(TrajectoryAnalysisConfig)
        except Exception as exc:  # noqa: BLE001  ConfigModule 未装配时降级默认
            logger.info(
                "[task-persistence] TrajectoryAnalysisConfig 未绑定 → 使用默认配置:%s: %s",
                type(exc).__name__, exc,
            )
            config = None
        return TaskTrajectoryAnalyzer(bot=bot, config=config)

    @singleton
    @provider
    def task_trajectory_service(
        self, injector: Injector
    ) -> TaskTrajectoryServiceProtocol:
        """Construct the trajectory service facade (REQ-8, P5b consumption layer).

        Wires the P4 assembler + P1b repo + P5a analyzer + deployment config
        into the single ``get_trajectory(task_id, *, do_analysis)`` entrypoint.
        The assembler / repo / analyzer are bound just above (or via the
        ``task_trajectory_analyzer`` provider); the config is OPTIONAL — a
        lightweight DI injector that did not bind ``TrajectoryAnalysisConfig``
        (via ``TaskTrajectoryConfigModule``) gets ``config=None``, which the
        service treats as "analysis bot not configured" → ``do_analysis=true``
        raises ``TrajectoryAnalysisNotConfiguredError`` (503); the default read
        path (``do_analysis=false``) never touches the config.

        Bound here (alongside the assembler/analyzer) so the trajectory read +
        analysis + service wiring lives in one module. ``get_trajectory`` is
        async — invokable from the async router handler.
        """
        config: TrajectoryAnalysisConfig | None
        try:
            config = injector.get(TrajectoryAnalysisConfig)
        except Exception as exc:  # noqa: BLE001  ConfigModule 未装配时降级 None
            logger.info(
                "[task-persistence] TrajectoryAnalysisConfig 未绑定 → 轨迹服务分析 bot 未配置:%s: %s",
                type(exc).__name__, exc,
            )
            config = None
        return TaskTrajectoryService(
            assembler=injector.get(TaskTrajectoryAssembler),
            repo=injector.get(TaskTrajectoryRepositoryProtocol),
            analyzer=injector.get(TaskTrajectoryAnalyzer),
            config=config,
        )
