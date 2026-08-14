"""TaskModule — production singletons for the task goal-driven execution framework。

装配 TaskGraphService(local in-mem)+ TaskService(facade;内部 ExecutionEngine)。
引擎构造期收传输端口(bot/bcs/discover),由 DI 从配置注入;端口缺省(community 未配 BaaS/BCS 密钥)
→ 引擎退化为纯内核路径(默认空端口规划/派发 stub),prod 由 corp overlay 注真实端口实现。

注册 TaskServiceProtocol / TaskLoopCallbackProtocol 供 http adapter 注入(Rule 14:DI composition root)。
"""
from __future__ import annotations

import os

from injector import Binder, Module, inject, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.api.task.task_loop_callback import TaskLoopCallbackProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)


def _task_engine_active() -> bool:
    """TASK_ENGINE=skill(或 truthy)激活真实端口接线;缺省 = 纯内核 stub 路径。"""
    return bool(os.environ.get("TASK_ENGINE", "").strip().lower() in {"skill", "true", "1"})


class TaskModule(Module):
    """Production bindings for the task module(社区核心,所有 profile 装配)。"""

    def configure(self, binder: Binder) -> None:
        # TaskGraphService 是 in-mem 单例(无外部依赖),直接 self-bind。
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
    ) -> TaskService:
        """构造 TaskService facade(引擎自当 ResultSink/TaskContextBuilder;构造期收端口)。

        端口接线策略:
        - TASK_ENGINE 激活且 corp 配置存在 → 构造 OpenApiBotAdapter/BcsHttpAdapter 注入(connector 层);
          community 无 corp 密钥时不接端口(留 None),引擎仍可用内核纯规划/派发 stub 路径。
        - discover(BotDiscoverServiceProtocol,来自 BotPublicModule)始终传入:
          SearchBasedDispatchStrategy 在端口齐全时才调,否则 stub MISS。
        """
        bot, bcs = self._resolve_ports()
        discover_port = self._resolve_discover(default=discover, bot_public=bot_public)
        return TaskService(graph, bot=bot, bcs=bcs, discover=discover_port)

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
    def callback_authenticator(
        self, auth: NoopCallbackAuthenticator
    ) -> CallbackAuthenticator:
        """回调鉴权(社区 Noop 直通;corp adapter 覆写绑 HmacCallbackAuthenticator + 密钥)。"""
        return auth

    @staticmethod
    def _resolve_ports():
        """构造传输端口(community:默认 None=纯内核;corp 经 overlay 覆写本方法注真实 conn)。

        环境路由(组合根选实现,不在 adapter 内 if):
        - ``OPENAPI_BOT_MODE=singlebox`` + ``TASK_ENGINE=skill`` → ``SingleboxEngineAdapter``(直连 per-bot
          引擎 WebSocket,绕开 BaaS;coop_group bcs 用 ``_DoubleBcsClient`` 占位,本轮 singlebox 走 single_bot)。
        - 否则 community 不内联 BaaS/BCS 密钥(Rule:环境访问在 DI),真实端口由 corp adapter 层覆写。
        """
        if not _task_engine_active():
            return None, None
        if os.environ.get("OPENAPI_BOT_MODE", "").strip().lower() == "singlebox":
            from agentclaw.community.core.task.task_runner.integration.double.double_bcs_client import (
                _DoubleBcsClient,
            )
            from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
                SingleboxEngineAdapter,
            )
            backend = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
            user_id = os.environ.get("SINGLEBOX_USER_ID", "146836")
            return SingleboxEngineAdapter(backend_base_url=backend, user_id=user_id), _DoubleBcsClient()
        # corp 接真实 OpenApiBotAdapter/BcsHttpAdapter 时,在 corp overlay 覆写本 provider 或经 env_url。
        # community 默认:即便 TASK_ENGINE 开,也无端口实现 → 退化 stub(可被测试用 double 覆盖)。
        return None, None

    @staticmethod
    def _resolve_discover(
        *,
        default: BotDiscoverServiceProtocol,
        bot_public: BotPublicServiceProtocol,
    ) -> BotDiscoverServiceProtocol:
        """选 bot 搜推端口(组合根选实现,不在策略内 if)。

        - ``OPENAPI_BOT_MODE=singlebox`` + ``TASK_ENGINE=skill`` → ``SingleboxKeywordBotDiscover``:
          本地关键字搜索(DB LIKE bot_name/owner_name,``/api/v1/bot-public/search``),
          因 singlebox 无 BCSFuse 索引服务,本地新建 bot 上不了 BCSFuse recommend 检索。
        - 其它(corp/prod)→ 注入的 BCSFuse ``BotDiscoverService``(语义 recommend)。
        """
        if not _task_engine_active():
            return default
        if os.environ.get("OPENAPI_BOT_MODE", "").strip().lower() == "singlebox":
            from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
                SingleboxKeywordBotDiscover,
            )
            return SingleboxKeywordBotDiscover(bot_public)  # type: ignore[arg-type]
        return default
