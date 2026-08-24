"""TaskModule — production singletons for the task goal-driven execution framework。

装配 TaskGraphService(local in-mem)+ TaskService(facade;内部 ExecutionEngine)。
引擎构造期收传输端口(bot/bcs/discover),由 DI 从配置注入;端口缺省(community 未配 BaaS/BCS 密钥)
→ 引擎退化为纯内核路径(默认空端口规划/派发 stub),prod 由 corp overlay 注真实端口实现。

注册 TaskServiceProtocol / TaskLoopCallbackProtocol 供 http adapter 注入(Rule 14:DI composition root)。
"""
from __future__ import annotations

import os

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.task.task_loop_callback import TaskLoopCallbackProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol,
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
    TaskNodeRepositoryProtocol,
    TaskNodeRunInfoRepositoryProtocol,
)
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_center.recovery_lifecycle import (
    TaskRecoveryLifecycle,
)
from agentclaw.community.core.task.task_harness.harness import TaskHarness
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.di.profile import DeployProfile


class TaskModule(Module):
    """Production bindings for the task module(社区核心,所有 profile 装配)。"""

    def configure(self, binder: Binder) -> None:
        # TaskGraphService remains directly constructible for lightweight test
        # injectors. The full composition root attaches the graph repository in
        # the task_service provider below.
        binder.bind(TaskGraphService, to=TaskGraphService, scope=singleton)
        # task_loop inbound callback 服务的进程内可信默认绑定(社区分布)。
        # CORP/prod 的 HmacCallbackAuthenticator + 真实密钥由 corp adapter 覆写(经模块替换/子类)。
        binder.bind(InMemoryCallbackCorrelationRegistry,
                    to=InMemoryCallbackCorrelationRegistry, scope=singleton)
        binder.bind(NoopCallbackAuthenticator, to=NoopCallbackAuthenticator, scope=singleton)

    @singleton
    @provider
    @inject
    def task_service(
        self,
        graph: TaskGraphService,
        discover: BotDiscoverServiceProtocol,
        bot_public: BotPublicServiceProtocol,
        injector: Injector,
    ) -> TaskService:
        """构造 TaskService facade(引擎自当 ResultSink/TaskContextBuilder;构造期收端口)。

        端口接线策略(组合根按 ``DEPLOY_PROFILE`` 选实现,不在 adapter 内 if):
        - ``DEPLOY_PROFILE=singlebox`` → singlebox 真实链路(``SingleboxEngineAdapter`` 直连 per-bot 引擎 +
          ``BcsHttpAdapter`` 复用 BCS REST 直连本地 BCS :21000);本地集成即真实执行。
        - 其它(corp/prod 由 overlay 覆写)→ community 不内联 BaaS/BCS 密钥,留 None,真实端口由 corp adapter 覆写。
        - discover(``BotDiscoverServiceProtocol``,来自 BotPublicModule)始终传入:
          singlebox profile 换 ``SingleboxKeywordBotDiscover``(本地关键字搜索),其余用注入的 BCSFuse。
        """
        try:
            graph.bind_repository(injector.get(TaskGraphRepositoryProtocol))
        except Exception:  # noqa: BLE101 standalone/lightweight test injector
            pass
        bot, bcs = self._resolve_ports()
        discover_port = self._resolve_discover(default=discover, bot_public=bot_public)
        # BBS 候选查询复用 BcnService 的统一 provider 身份(BcnConfig prod/pre,与 register/switch
        # provider-bot 同源)。任务模块作为普通消费方经 DI 注入 BcnService;纯内核/未装 BotManagement 的
        # DI 测试路径取不到 → None(BBS 按可恢复态跳过;singlebox 无凭据亦走 not-configured 静默)。
        try:
            bcn = injector.get(BcnService)
        except Exception:  # noqa: BLE001 未绑定 → 跳过 BBS roster
            bcn = None
        bcs_identity = None
        if bcs is not None:
            from agentclaw.community.core.task.task_runner.integration.bcs_bot_identity_resolver import (
                BotServiceBcsBotIdentityResolver,
            )
            # BCS 建群时才需要跨模块解析 owner；纯内核/HTTP contract 测试不强制装配 Bot 模块。
            bcs_identity = BotServiceBcsBotIdentityResolver(
                injector.get(BotServiceProtocol)
            )
        # harness 旁路常驻巡检(SLA 超时复位 / FAILED 重派重试 / PENDING 派发超时重搜推);
        # facade 内部 set_on_harness 回填编排核入口并启动 daemon 巡检线程。
        harness = TaskHarness(graph)
        # TaskPersistenceModule is optional for the pure-core and lightweight DI
        # test paths. Resolve every persistence port lazily so Injector never
        # attempts to instantiate an abstract repository protocol.
        try:
            task_info_repo = injector.get(TaskInfoRepositoryProtocol)
        except Exception:  # noqa: BLE101 未绑定 → execute 跳过 task_info 落库
            task_info_repo = None
        # 回投落库:TaskPersistenceModule 装了即取到(与 task_info_repo 同模块绑定);测试/纯内核
        # fixture 若未装则取不到 → 跳过回投落库(与 task_info_repo 缺省同语义,不阻断编排核推进)。
        try:
            callback_repo = injector.get(TaskCallbackRepositoryProtocol)
        except Exception:  # noqa: BLE101 未绑定 → 跳过回投落库
            callback_repo = None
        # task_node / task_node_run_info 落库(workflow/yaml 分支):TaskPersistenceModule 装了即取到
        # (与 task_info_repo/callback_repo 同模块绑定);测试/纯内核 fixture 未装则取不到 → 跳过节点落库
        # (与 task_info_repo 缺省同语义,不阻断编排核推进;dynamic 分支本就不落这两个表)。
        try:
            task_node_repo = injector.get(TaskNodeRepositoryProtocol)
        except Exception:  # noqa: BLE101 未绑定 → 跳过 task_node 落库
            task_node_repo = None
        try:
            task_node_run_info_repo = injector.get(TaskNodeRunInfoRepositoryProtocol)
        except Exception:  # noqa: BLE101 未绑定 → 跳过 task_node_run_info 落库
            task_node_run_info_repo = None
        try:
            bot_service = injector.get(BotServiceProtocol)
        except Exception:  # noqa: BLE101 未绑定 → dashboard 不附加 assignee 的 bot 归属/名
            bot_service = None
        # 回投地址 = 本 backend 自身访问 URL(singlebox→SINGLEBOX_BACKEND_URL / 其余→BACKEND_URL,
        # 单值、各环境部署 overlay 注入当前环境 backend 地址),agent 回投结果往此 origin POST(自行拼 /api/v1/...)。
        return TaskService(
            graph, harness=harness, bot=bot, bcs=bcs, discover=discover_port,
            bcn=bcn, bcs_identity=bcs_identity, task_info_repo=task_info_repo,
            callback_repo=callback_repo, task_node_repo=task_node_repo,
            task_node_run_info_repo=task_node_run_info_repo,
            bot_service=bot_service,
            api_base_url=self._resolve_api_base_url(),
        )

    @singleton
    @provider
    @inject
    def task_service_protocol(self, svc: TaskService) -> TaskServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def task_loop_callback_protocol(self, svc: TaskService) -> TaskLoopCallbackProtocol:
        """回投 Protocol = TaskService.callback(已 internal 持 TaskLoopCallback)。"""
        return svc.callback

    @singleton
    @provider
    @inject
    def callback_correlation_registry(
        self, reg: InMemoryCallbackCorrelationRegistry
    ) -> CallbackCorrelationRegistry:
        """task 级回调→节点寻址 registry(社区 in-mem;进程内可信)。"""
        return reg

    @singleton
    @provider
    @inject
    def task_recovery_lifecycle(self, injector: Injector) -> TaskRecoveryLifecycle:
        """Recovery lifecycle participant — picked up by discover_lifecycle_participants.

        Construction is cheap (holds the injector only); the worker's graph
        repository + task service are resolved lazily on first scan. The
        participant's ``startup()`` no-ops unless ``TASK_RECOVERY_ENABLED=1``."""
        return TaskRecoveryLifecycle(injector)

    @singleton
    @provider
    @inject
    def callback_authenticator(
        self, auth: NoopCallbackAuthenticator
    ) -> CallbackAuthenticator:
        """回调鉴权(社区 Noop 直通;corp adapter 覆写绑 HmacCallbackAuthenticator + 密钥)。"""
        return auth

    @staticmethod
    def _resolve_api_base_url() -> str:
        """返回本 backend 自身访问 URL(agent 回投结果往此 origin POST,自行拼 /api/v1/... 路径)。

        复用 task_discovery 既有约定,不引入新 env:单值 ``BACKEND_URL``(非 singlebox,各环境部署
        overlay 注入当前环境 backend 地址,如预发注入预发后端;空 → localhost:8888 表示未配置/本地默认),
        ``SINGLEBOX_BACKEND_URL``(singlebox 直连,与 _resolve_ports 同源)。代码不按 env 分支:环境差异
        由 overlay 注入的单值决定,符合组合根裸 env 读取约束。"""

        if os.environ.get("DEPLOY_PROFILE", "").strip().lower() == DeployProfile.SINGLEBOX.value:
            return os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
        return os.environ.get("BACKEND_URL", "http://localhost:8888")

    @staticmethod
    def _resolve_ports():
        """构造传输端口(组合根按 ``DEPLOY_PROFILE`` 选实现,不在 adapter 内 if)。

        - ``DEPLOY_PROFILE=singlebox`` → ``SingleboxEngineAdapter``(直连 per-bot 引擎 WebSocket,绕开 BaaS)
          + ``SingleboxBcsAdapter``(继承 ``BcsHttpAdapter`` 复用 BCS REST 直连本地 BCS :21000;本地
          ``require_authentication=false``,HMAC 头被忽略;仅覆写本地响应形状与生产不一致处 → coop_group 真驱动本地 BCS)。
        - 其它(corp/prod 由 overlay 覆写)→ 不内联 BaaS/BCS(社区不发 corp 密钥),真实端口由 corp adapter
          完成装配。
        """
        if os.environ.get("DEPLOY_PROFILE", "").strip().lower() != DeployProfile.SINGLEBOX.value:
            return None, None
        from agentclaw.community.core.task.task_runner.integration.bcs_token_provider import (
            LocalBcsTokenProvider,
        )
        from agentclaw.community.core.task.task_runner.integration.singlebox_bcs_adapter import (
            SingleboxBcsAdapter,
        )
        from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
            SingleboxEngineAdapter,
        )
        backend = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
        user_id = os.environ.get("SINGLEBOX_USER_ID", "146836")
        bot = SingleboxEngineAdapter(backend_base_url=backend, user_id=user_id)
        # 本地 BCS 与生产同 REST、require_authentication=false → SingleboxBcsAdapter(继承 BcsHttpAdapter,
        # HMAC 头被本地忽略;仅覆写本地响应形状差异)。_DoubleBcsClient 仅留单测用。
        # SINGLEBOX_BCS_DOUBLE=1 → 用 _DoubleBcsClient 模拟(立即 completed + success output),
        # 供 e2e 跑协作群真链路而不依赖真 BCS 群聊时序(singlebox 无真群协作收敛保障):
        # 真实 form_group/session/poll 路径 + 确定终态回投 PASS(经 BcsSessionTranslator 解析 output 为 success json)。
        if os.environ.get("SINGLEBOX_BCS_DOUBLE", "").strip().lower() in {"1", "true"}:
            from agentclaw.community.core.task.task_runner.integration.double.double_bcs_client import (
                _DoubleBcsClient,
            )
            _coop_pass_output = '{"success": true, "data": "coop_group_done"}'
            bcs = _DoubleBcsClient(
                session_status="completed", session_output=_coop_pass_output,
                sm_status="completed", sm_output=_coop_pass_output,
                poll_once_then_terminal=True, terminal_after=1,
            )
            # double 不连接真实 BCS，任务模式候选固定返回空列表。
        else:
            token = LocalBcsTokenProvider.from_env()
            bcs = SingleboxBcsAdapter(token)
        return bot, bcs

    @staticmethod
    def _resolve_discover(
        *,
        default: BotDiscoverServiceProtocol,
        bot_public: BotPublicServiceProtocol,
    ) -> BotDiscoverServiceProtocol:
        """选 bot 搜推端口(组合根按 ``DEPLOY_PROFILE`` 选实现)。

        - ``DEPLOY_PROFILE=singlebox`` → ``SingleboxKeywordBotDiscover``:本地关键字搜索(DB LIKE bot_name/
          owner_name,``/api/v1/bot-public/search``);singlebox 无 BCSFuse 索引服务,本地新建 bot 上不了 recommend。
        - 其它(corp/prod)→ 注入的 BCSFuse ``BotDiscoverService``(语义 recommend)。
        """
        if os.environ.get("DEPLOY_PROFILE", "").strip().lower() != DeployProfile.SINGLEBOX.value:
            return default
        from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
            SingleboxKeywordBotDiscover,
        )
        return SingleboxKeywordBotDiscover(bot_public)  # type: ignore[arg-type]
