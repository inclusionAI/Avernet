"""TaskModule — production singletons for the task goal-driven execution framework。

装配 TaskGraphService(local in-mem)+ TaskService(facade;内部 ExecutionEngine)。
引擎构造期收传输端口(bot/bcs/discover),由 DI 从配置注入;端口缺省(community 未配 BaaS/BCS 密钥)
→ 引擎退化为纯内核路径(默认空端口规划/派发 stub),prod 由 corp overlay 注真实端口实现。

注册 TaskServiceProtocol / TaskLoopCallbackProtocol 供 http adapter 注入(Rule 14:DI composition root)。
"""

from __future__ import annotations

import os
import logging
from urllib.parse import urlparse

from injector import Binder, Injector, Module, inject, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator,
    NoopCallbackAuthenticator,
)
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.system_config_service import SystemConfigServiceProtocol
from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    HARNESS_POLLER,
    SEARCH_SKILL,
    SKILL_REPORT,
    TaskClaimJoinGate,
    TaskClaimJoinGateProtocol,
    TaskSettingsService,
    TaskSettingsServiceProtocol,
)
from agentclaw.community.api.task.task_grant_service import (
    TaskClaimGrantService,
    TaskClaimGrantServiceProtocol,
)
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
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
    BcsTokenProvider,
    LocalBcsTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
    InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.task.task_runner.client.ports import (
    BcsClientPort,
    OpenApiBotPort,
)
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin
from agentclaw.community.di.config import TaskDispatchConfig
from agentclaw.community.di.profile import DeployProfile

logger = logging.getLogger("task.module")


def _harness_enabled() -> bool:
    """TaskHarness 旁路巡检开关(env ``OCB_TASK_HARNESS_ENABLED``,默认**关闭**)。

    harness poller(SLA 超时复位 / FAILED 重派重试 / PENDING 派发超时重搜推)为旁路常驻线程;
    默认关闭,facade 以事件驱动(on_execute/on_report/on_pass/on_miss)为主推进。需要旁路兜底
    (bot 崩溃/SLA 超时/派发卡住)时显式置 ``OCB_TASK_HARNESS_ENABLED=1`` 启用。harness=None 时
    TaskService 不启动 daemon 巡检线程(见 task_service 装配处 ``if self._harness is not None``)。"""
    return os.environ.get("OCB_TASK_HARNESS_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _resolve_harness_enabled(
    task_settings: TaskSettingsServiceProtocol | None,
) -> bool:
    """harness 旁路巡检开关:优先读 tasks/settings(``harness_poller``,system-config KV,跨副本热改),
    未绑/读异常时回退 env ``OCB_TASK_HARNESS_ENABLED``。tasks/settings(`harness_poller`)默认**开启**;
    env 兜底(仅 task_settings 未绑时)默认关闭,保持轻量/测试无 daemon 巡检线程。

    Settings 走 KV 可运行时经 POST /tasks/settings 热改;env 仅本地/单进程兜底。
    当 TaskSettingsServiceProtocol 未绑(纯内核/轻量测试)时直接回退 env,不影响既有本地开关语义。
    """
    if task_settings is not None:
        try:
            return task_settings.is_enabled(HARNESS_POLLER)
        except Exception as exc:  # noqa: BLE001 设置读取失败 → 回退 env
            logger.warning(
                "[task][task-module] harness_poller 设置读取失败,回退 env:%s",
                exc,
            )
    return _harness_enabled()


class TaskModule(Module):
    """Production bindings for the task module(社区核心,所有 profile 装配)。"""

    def configure(self, binder: Binder) -> None:
        # TaskGraphService remains directly constructible for lightweight test
        # injectors. The full composition root attaches the graph repository in
        # the task_service provider below.
        binder.bind(TaskGraphService, to=TaskGraphService, scope=singleton)
        # task_loop inbound callback 服务的进程内可信默认绑定(社区分布)。
        # CORP/prod 的 HmacCallbackAuthenticator + 真实密钥由 corp adapter 覆写(经模块替换/子类)。
        binder.bind(
            InMemoryCallbackCorrelationRegistry,
            to=InMemoryCallbackCorrelationRegistry,
            scope=singleton,
        )
        binder.bind(
            NoopCallbackAuthenticator, to=NoopCallbackAuthenticator, scope=singleton
        )

    @singleton
    @provider
    @inject
    def callback_data_enricher(self, injector: Injector) -> CallbackDataEnricher:
        """回投数据 enricher(BCN 查 BCS run 详情 + ClawMind 构图);base_url 取自 BcsTokenProvider。

        corp overlay 经 DI 绑定 BcsTokenProvider(``_RealToken``);community/singlebox 未绑 →
        ``LocalBcsTokenProvider.from_env()``(``SINGLEBOX_BCS_URL``)。与 BcsClientPort 同款注入。
        """
        try:
            token = injector.get(BcsTokenProvider)
        except Exception:  # noqa: BLE001 community/singlebox 未绑 BcsTokenProvider → singlebox fallback
            token = LocalBcsTokenProvider.from_env()
        return CallbackDataEnricher(token)

    @singleton
    @provider
    @inject
    def task_service(
        self,
        graph: TaskGraphService,
        bot_public: BotPublicServiceProtocol,
        task_dispatch: TaskDispatchConfig,
        injector: Injector,
    ) -> TaskService:
        """构造 TaskService facade(引擎自当 ResultSink/TaskContextBuilder;构造期收端口)。

        端口接线策略(组合根按 ``DEPLOY_PROFILE`` 选实现,不在 adapter 内 if):
        - ``DEPLOY_PROFILE=singlebox`` → singlebox 真实链路(``SingleboxEngineAdapter`` 直连 per-bot 引擎 +
          ``BcsHttpAdapter`` 复用 BCS REST 直连本地 BCS :21000);本地集成即真实执行。
        - 其它(corp/prod)→ 不内联 BaaS/BCS 密钥;``_resolve_ports`` 返 ``(None,None)`` 后由
          ``injector.get(OpenApiBotPort)``/``injector.get(BcsClientPort)`` 取 corp overlay 经 DI 绑定的
          真实端口实现(community 未绑 → None,纯内核/HTTP-contract 路径退化为 stub)。
        - discover: every profile reuses ``SingleboxKeywordBotDiscover`` over the local
          public-Bot catalogue (name/owner-name LIKE). Task dispatch intentionally does
          not invoke BCSFuse recommendation, whose availability must not decide routing.
        """
        try:
            graph.bind_repository(injector.get(TaskGraphRepositoryProtocol))
        except Exception:  # noqa: BLE101 standalone/lightweight test injector
            pass
        bot, bcs = self._resolve_ports()
        logger.info(
            "[task][task-module] _resolve_ports → bot=%s bcs=%s",
            type(bot).__name__ if bot is not None else "None",
            type(bcs).__name__ if bcs is not None else "None",
        )
        # 非 singlebox(corp/prod):corp overlay 经 DI 绑定 OpenApiBotPort/BcsClientPort(真实 BaaS/BCS
        # 凭据),构造期取用。community 未绑 → None(与 BcnService 同款 try/except 降级;失败打 WARNING),
        # 纯内核/HTTP-contract 测试不阻断。singlebox 已由 _resolve_ports 给出真实端口,跳过。
        if bot is None:
            try:
                bot = injector.get(OpenApiBotPort)
                logger.info(
                    "[task][task-module] OpenApiBotPort DI 注入=%s",
                    type(bot).__name__ if bot is not None else "None(provider 返 None)",
                )
            except Exception as exc:  # noqa: BLE001 未绑定 → 单 bot 派发端口缺省(打 WARNING 暴露)
                logger.warning(
                    "[task][task-module] OpenApiBotPort DI 未绑定/解析失败 → 单 bot 端口缺省:%s: %s",
                    type(exc).__name__,
                    exc,
                )
                bot = None
        if bcs is None:
            try:
                bcs = injector.get(BcsClientPort)
                logger.info(
                    "[task][task-module] BcsClientPort DI 注入=%s",
                    type(bcs).__name__ if bcs is not None else "None(provider 返 None)",
                )
            except Exception as exc:  # noqa: BLE001 未绑定 → 协作群协调端口缺省(打 WARNING 暴露)
                logger.warning(
                    "[task][task-module] BcsClientPort DI 未绑定/解析失败 → 协作群端口缺省:%s: %s",
                    type(exc).__name__,
                    exc,
                )
                bcs = None
        logger.info(
            "[task][task-module] 端口装配结果 bot=%s bcs=%s → execution_backend 将%s真装配",
            type(bot).__name__ if bot is not None else "None",
            type(bcs).__name__ if bcs is not None else "None",
            "" if (bot is not None and bcs is not None) else "不(全退 Avernet 桩)",
        )
        discover_port = self._resolve_discover(bot_public=bot_public)
        # BBS 候选查询复用 BcnService 的统一 provider 身份(BcnConfig prod/pre,与 register/switch
        # provider-bot 同源)。任务模块作为普通消费方经 DI 注入 BcnService;纯内核/未装 BotManagement 的
        # DI 测试路径取不到 → None(BBS 按可恢复态跳过;singlebox 无凭据亦走 not-configured 静默)。
        try:
            bcn = injector.get(BcnService)
        except Exception:  # noqa: BLE001 未绑定 → 跳过 BBS roster
            bcn = None
        bcs_identity = None
        if bcs is not None:
            from agentclaw.community.core.task.task_runner.client.bcs_bot_identity_resolver import (
                BotServiceBcsBotIdentityResolver,
            )

            # BCS 建群时才需要跨模块解析 owner；纯内核/HTTP contract 测试不强制装配 Bot 模块。
            bcs_identity = BotServiceBcsBotIdentityResolver(
                injector.get(BotServiceProtocol)
            )
        # 任务开关服务(tasks/settings):system-config KV,跨副本共享;harness 旁路巡检开关经此热改。
        # 未绑(纯内核/轻量测试)→ None → harness 回退 env 决策。任务派发链路也消费同一实例。
        try:
            task_settings = injector.get(TaskSettingsServiceProtocol)
        except Exception as exc:  # noqa: BLE001 未绑定 → 使用静态默认值
            logger.info(
                "[task][task-module] TaskSettingsServiceProtocol 未绑定 → 使用静态默认值:%s",
                exc,
            )
            task_settings = None
        # harness 旁路常驻巡检(SLA 超时复位 / FAILED 重派重试 / PENDING 派发超时重搜推);
        # 可配置开关,默认开启(tasks/settings `harness_poller`):旁路巡检常驻兜底(SLA 超时复位/FAILED
        # 重派/PENDING 派发超时重搜推);harness=None(未绑且 env 关)时不启动 daemon 巡检线程。
        # 优先读 tasks/settings(harness_poller,KV 跨副本热改),未绑/读异常回退 env OCB_TASK_HARNESS_ENABLED。
        if _resolve_harness_enabled(task_settings):
            harness = TaskHarness(graph)
            logger.info("[task][task-module] TaskHarness 旁路巡检已启用")
        else:
            harness = None
            logger.info("[task][task-module] TaskHarness 旁路巡检已关闭(默认)")
        # TaskPersistenceModule is optional for the pure-core and lightweight DI
        # test paths. Resolve every persistence port lazily so Injector never
        # attempts to instantiate an abstract repository protocol.
        try:
            task_info_repo = injector.get(TaskInfoRepositoryProtocol)
        except Exception:  # noqa: BLE101 未绑定 → execute 跳过 task_info 落库
            task_info_repo = None
        # 回投落库:TaskPersistenceModule 装了即取到(与 task_info_repo 同模块绑定);测试/纯内核
        # fixture 若未装则取不到 → 跳过回投落库(与 task_info_repo 缺省同语义,不阻断编排核推进)。
        if task_info_repo is not None:
            graph.bind_task_info_repository(task_info_repo)
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
        try:
            staff_dept = injector.get(StaffDeptPlugin)
        except Exception:  # noqa: BLE101 未绑定 → list 不附加 owner_user_name
            staff_dept = None
        # claim_on JOIN 灰度开关(默认关闭,HTTP 显式开启):经 task_claim_join_gate provider 解析
        # (系统配置 KV,跨副本共享);未绑(纯内核/轻量测试)→ None → gate 关 → 派发不做 claim_on 交集(不回归)。
        try:
            task_auth_gate = injector.get(TaskClaimJoinGateProtocol)
        except Exception as exc:  # noqa: BLE101 未绑 → gate None(fail-open 关)
            logger.info(
                "[task][task-module] TaskClaimJoinGateProtocol 未绑定 → claim_on JOIN 开关缺省(关):%s:"
                " %s",
                type(exc).__name__,
                exc,
            )
            task_auth_gate = None
        # 任务回投 origin 使用 bcs_client.task_callback_url[_pre]，由 BCS client
        # 按当前环境选出对应地址。它是 BCS → Avernet 的真实回投通道，不能复用
        # economy_governance 的卡片回调地址。
        bcs_callback_url = ""
        if bcs is not None:
            _callback_fn = getattr(bcs, "task_callback_url", None)
            if callable(_callback_fn):
                bcs_callback_url = str(_callback_fn() or "").strip()
        from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
            BcsBotTokenProvider, NullBcsBotTokenProvider,
        )
        try:
            bot_token_provider = injector.get(BcsBotTokenProvider)
        except Exception:  # noqa: BLE101 未绑定时降级为无 token provider
            bot_token_provider = NullBcsBotTokenProvider()
        from agentclaw.community.core.task.task_discovery.notify_messages_provider import (
            NotifyMessagesProvider, NullNotifyMessagesProvider,
        )
        try:
            notify_messages_provider = injector.get(NotifyMessagesProvider)
        except Exception:  # noqa: BLE101 未绑定时降级 noop(不阻断)
            notify_messages_provider = NullNotifyMessagesProvider()
        return TaskService(
            graph,
            harness=harness,
            bot=bot,
            bcs=bcs,
            discover=discover_port,
            bcn=bcn,
            bcs_identity=bcs_identity,
            task_info_repo=task_info_repo,
            callback_repo=callback_repo,
            task_node_repo=task_node_repo,
            task_node_run_info_repo=task_node_run_info_repo,
            bot_service=bot_service,
            staff_dept=staff_dept,
            task_auth_gate=task_auth_gate,
            api_base_url=self._resolve_api_base_url(bcs_callback_url),
            bot_token_provider=bot_token_provider,
            notify_messages_provider=notify_messages_provider,
            task_search_skill_enabled=task_dispatch.task_search_skill_enabled,
            task_settings=task_settings,
        )

    @singleton
    @provider
    @inject
    def task_service_protocol(self, svc: TaskService) -> TaskServiceProtocol:
        return svc

    @singleton
    @provider
    @inject
    def task_claim_grant_service(
        self, injector: Injector
    ) -> TaskClaimGrantServiceProtocol:
        """任务认领 Bot 授权服务:复用 corp overlay 绑定的 OpenApiBotPort(api_key/prefix/base_url,服务端持有)。

        OpenApiBotPort 仅 corp/prod 经 overlay 绑定(community/singlebox 无 secbaas api-key)→ 未绑时
        bot=None,grant/revoke 显式报错(本地路径本就不调 grant 端点)。cookie/referer 取自入站请求头(不在 DI)。
        stateless:不落本地表,api-key 不暴露前端。"""
        try:
            bot = injector.get(OpenApiBotPort)
            logger.info(
                "[task][task-module] grant service OpenApiBotPort 注入=%s",
                type(bot).__name__ if bot is not None else "None",
            )
        except Exception as exc:  # noqa: BLE101 community/singlebox 无 secbaas 绑定 → bot=None
            logger.info(
                "[task][task-module] grant service OpenApiBotPort 未绑定 → bot=None(%s)",
                exc,
            )
            bot = None
        return TaskClaimGrantService(bot=bot)

    @singleton
    @provider
    @inject
    def task_settings_service(
        self,
        injector: Injector,
        task_dispatch: TaskDispatchConfig,
    ) -> TaskSettingsServiceProtocol:
        """Generic runtime task switches backed by SystemConfigService KV."""
        try:
            config = injector.get(SystemConfigServiceProtocol)
        except Exception as exc:  # noqa: BLE001 lightweight/community path
            logger.info(
                "[task][task-module] SystemConfigServiceProtocol 未绑定 → task settings 使用默认值:%s",
                exc,
            )
            config = None
        return TaskSettingsService(
            config=config,
            defaults={
                SEARCH_SKILL: task_dispatch.task_search_skill_enabled,
                SKILL_REPORT: task_dispatch.skill_report_enabled,
            },
        )

    @singleton
    @provider
    @inject
    def task_claim_join_gate(
        self, settings: TaskSettingsServiceProtocol
    ) -> TaskClaimJoinGateProtocol:
        """Compatibility adapter for the claim_on JOIN dispatch filter."""
        return TaskClaimJoinGate(settings=settings)

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
    def _resolve_api_base_url(task_callback_url: str = "") -> str:
        """返回本 backend 自身访问 URL(agent 回投结果往此 origin POST,自行拼 /api/v1/... 内部路径)。

        解析 ``bcs_client.task_callback_url[_pre]`` 提供的任务回投 origin。BCS client
        已按当前环境选择 ``task_callback_url_pre`` 或 ``task_callback_url``；这里仅取
        ``scheme://netloc``，避免把路径误拼到任务 callback endpoint。

        - singlebox(``DEPLOY_PROFILE``) → ``SINGLEBOX_BACKEND_URL``/localhost(本地直连);
        - 其余 → 解析 ``bcs_client.task_callback_url[_pre]`` 的 origin;
        - 空值/非法(社区/dev/未配置 BCS 回投地址)→ 回退 localhost:8888。"""
        if (
            os.environ.get("DEPLOY_PROFILE", "").strip().lower()
            == DeployProfile.SINGLEBOX.value
        ):
            return os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
        if not task_callback_url:
            return "http://localhost:8888"
        parsed = urlparse(task_callback_url)
        if not parsed.scheme or not parsed.netloc:
            return "http://localhost:8888"
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _resolve_ports():
        """构造传输端口(组合根按 ``DEPLOY_PROFILE`` 选实现,不在 adapter 内 if)。

        - ``DEPLOY_PROFILE=singlebox`` → ``SingleboxEngineAdapter``(直连 per-bot 引擎 WebSocket,绕开 BaaS)
          + ``SingleboxBcsAdapter``(继承 ``BcsHttpAdapter`` 复用 BCS REST 直连本地 BCS :21000;本地
          ``require_authentication=false``,HMAC 头被忽略;仅覆写本地响应形状与生产不一致处 → coop_group 真驱动本地 BCS)。
        - 其它(corp/prod 由 overlay 覆写)→ 不内联 BaaS/BCS(社区不发 corp 密钥),真实端口由 corp adapter
          完成装配。
        """
        if (
            os.environ.get("DEPLOY_PROFILE", "").strip().lower()
            != DeployProfile.SINGLEBOX.value
        ):
            return None, None
        from agentclaw.community.core.task.task_runner.client.bcs_token_provider import (
            LocalBcsTokenProvider,
        )
        from agentclaw.community.core.task.task_runner.client.singlebox_bcs_adapter import (
            SingleboxBcsAdapter,
        )
        from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
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
            from agentclaw.community.core.task.task_runner.client.double.double_bcs_client import (
                _DoubleBcsClient,
            )

            _coop_pass_output = '{"success": true, "data": "coop_group_done"}'
            bcs = _DoubleBcsClient(
                session_status="completed",
                session_output=_coop_pass_output,
                sm_status="completed",
                sm_output=_coop_pass_output,
                poll_once_then_terminal=True,
                terminal_after=1,
            )
            # double 不连接真实 BCS，任务模式候选固定返回空列表。
        else:
            token = LocalBcsTokenProvider.from_env()
            bcs = SingleboxBcsAdapter(token)
        return bot, bcs

    @staticmethod
    def _resolve_discover(
        *, bot_public: BotPublicServiceProtocol
    ) -> BotDiscoverServiceProtocol:
        """Reuse the existing public-Bot LIKE candidate adapter for every profile.

        ``SearchBasedDispatchStrategy`` performs jieba tokenization and calls this
        port once per token. BCSFuse remains available to the separate public
        Bot-discovery API, but is not a task dispatch dependency.
        """
        from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
            SingleboxKeywordBotDiscover,
        )

        return SingleboxKeywordBotDiscover(bot_public)  # type: ignore[arg-type]
