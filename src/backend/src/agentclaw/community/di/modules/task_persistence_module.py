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
from agentclaw.community.core.repository.implementations.task.task_artifact_repository import (
    TaskArtifactRepository,
)
from agentclaw.community.core.repository.implementations.task.task_trajectory_repository import (
    TaskTrajectoryRepository,
)
from agentclaw.community.core.repository.protocols.platform import (
    SessionResourceRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.task import (
    TaskActionLogRepositoryProtocol,
    TaskArtifactRepositoryProtocol,
    TaskCallbackCorrelationRepositoryProtocol,
    TaskCallbackRepositoryProtocol,
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRelationRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
    TaskTrajectoryRepositoryProtocol,
)
from agentclaw.community.core.task.task_context.task_artifact.artifact_service import (
    TaskArtifactService,
    TaskArtifactServiceProtocol,
)
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
)
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_service import (
    TaskTrajectoryService,
    TaskTrajectoryServiceProtocol,
)
from agentclaw.community.core.task.task_context.task_context_service import TaskContextService
from agentclaw.community.core.task.task_context.task_graph_service import (  # noqa: E402
    TaskGraphService,
)
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (  # noqa: E402
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BcsClientPort,
    OpenApiBotPort,
)
from agentclaw.community.api.task.task_context_service import TaskContextServiceProtocol
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
        # Task artifact manifest repo (spec 2026-09-23-task-artifact-manifest,
        # PR2): immutable artifact rows; dedupe-idempotent create_or_get.
        binder.bind(
            TaskArtifactRepositoryProtocol,
            to=TaskArtifactRepository,
            scope=singleton,
        )

    @singleton
    @provider
    def task_artifact_service(
        self, injector: Injector
    ) -> "TaskArtifactServiceProtocol":
        """构造 Artifact 双写服务(spec 2026-09-23-task-artifact-manifest,PR3)。

        仓储必装配期为 ``configure()`` 里的 TaskArtifactRepositoryProtocol
        singleton;`SessionResourceRepositoryProtocol` 为可选(File 就绪闸用,
        SessionResourcesModule 绑定;轻量 injector 未装 → None → 文件引用一律
        拒发布 + WARNING,Text 双写不受影响)。
        """
        session_resources = None
        try:
            session_resources = injector.get(SessionResourceRepositoryProtocol)
        except Exception as exc:  # noqa: BLE101 轻量 DI 未装 SessionResourcesModule
            logger.info(
                "[task-persistence] SessionResourceRepositoryProtocol 未绑定"
                " → Artifact 文件分支就绪闸不可用(文件引用将拒发布):%s: %s",
                type(exc).__name__, exc,
            )
        return TaskArtifactService(
            repo=injector.get(TaskArtifactRepositoryProtocol),
            session_resource_repo=session_resources,
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
        # RUNNING 节点会话明细探测的两个可选协作方(决策 #14 精神:未接线 → 探测
        # 整体关闭,do_analysis 传 running_sessions=None,行为与探测前一致):
        # * TaskGraphService — 读 task_execution_graph 现场节点状态/session_id
        #   (TaskModule 单例绑定;轻量 injector 未装 → get 抛 → None);
        # * BcsClientPort — 经 BCS 拉会话明细(profile 模块绑定;community 提供
        #   方者在空配置下可能直接返回 None,同样自然关探测)。
        try:
            graph = injector.get(TaskGraphService)
        except Exception as exc:  # noqa: BLE001  轻量 DI 未装配 TaskModule
            logger.info(
                "[task-persistence] TaskGraphService 未绑定 → RUNNING 会话探测关闭:%s: %s",
                type(exc).__name__, exc,
            )
            graph = None
        try:
            bcs = injector.get(BcsClientPort)
        except Exception as exc:  # noqa: BLE001  轻量 DI 未装配 runner profile
            logger.info(
                "[task-persistence] BcsClientPort 未绑定 → RUNNING 会话探测关闭:%s: %s",
                type(exc).__name__, exc,
            )
            bcs = None
        # 持有者 bot session_token 解析(方案一:会话历史读口带参与者 Bearer,否则
        # BCS 401 "valid Human identity or Bot token required")。corp 注入直读
        # bcs_bots.session_token 的实现;community 默认 Null(恒 None → 裸 HMAC
        # 尝试,401 再降级);轻量 DI 未绑定 → None 同关。
        try:
            bcs_bot_tokens = injector.get(BcsBotTokenProvider)
        except Exception as exc:  # noqa: BLE001  未装配 provider
            logger.info(
                "[task-persistence] BcsBotTokenProvider 未绑定 → 会话历史匿名读取(401 降级):%s: %s",
                type(exc).__name__, exc,
            )
            bcs_bot_tokens = None
        # 产物 manifest 读时富化(spec 2026-09-23-task-artifact-manifest §4;样板
        # bcs_bot_tokens 的 try/except-get):优先取本模块的 ``task_artifact_service``
        # provider 单例;轻量 injector 未绑 → None → 末位事件 artifacts 保持 None
        # (缺字段=无信号),轨迹本体零变化。
        try:
            artifact_service = injector.get(TaskArtifactServiceProtocol)
        except Exception as exc:  # noqa: BLE001 未装配 artifact provider
            logger.info(
                "[task-persistence] TaskArtifactServiceProtocol 未绑定 → 轨迹读侧产物富化关闭:%s: %s",
                type(exc).__name__, exc,
            )
            artifact_service = None
        return TaskTrajectoryService(
            assembler=injector.get(TaskTrajectoryAssembler),
            repo=injector.get(TaskTrajectoryRepositoryProtocol),
            analyzer=injector.get(TaskTrajectoryAnalyzer),
            config=config,
            graph=graph,
            bcs=bcs,
            bcs_bot_tokens=bcs_bot_tokens,
            artifact_service=artifact_service,
        )

    @singleton
    @provider
    def task_context_service(
        self, injector: Injector
    ) -> TaskContextServiceProtocol:
        """Construct the ``task_context`` facade (spec 2026-09-18): the single对外
        entry for trajectory read + write, relaying to the internal
        ``TaskTrajectoryService`` (bound just above as
        ``TaskTrajectoryServiceProtocol``). External callers Inject
        ``TaskContextServiceProtocol`` (re-exported from
        ``api/task/task_context_service.py``): the 2 HTTP routers go to
        ``get_trajectory``; the engine / task_service / callback_adapter emission
        gates go to ``emit_trajectory_event``
        (fire-and-forget, decision #14). The trajectory repo + emit helpers stay
        internal to the ``task_context.task_trajectory`` sub-module.
        """
        return TaskContextService(
            trajectory_service=injector.get(TaskTrajectoryServiceProtocol),
        )
