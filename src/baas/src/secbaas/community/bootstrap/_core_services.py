from dependency_injector import containers, providers

from secbaas.community.api.health_check.bot import BotHealthCheckerConfig
from secbaas.community.core.service.api_gateway import (
    DefaultAPIKeyService,
    DefaultAPIKeyValidator,
)
from secbaas.community.core.service.auth_service import AuthService
from secbaas.community.core.service.bcn import DefaultBcnDownlinkService
from secbaas.community.core.service.bcn.uplink import (
    BcnUplinkCallback,
    BcnUplinkClient,
    BcnUplinkConfig,
)
from secbaas.community.core.service.bot_manage import (
    DefaultBotCrudService,
    DefaultBotManagementService,
)
from secbaas.community.core.service.bot_qpm import DefaultBotQpmManageService
from secbaas.community.core.service.bot_run import (
    AsyncChatClientPool,
    BaasBotService,
    BaasBotServiceConfig,
    BotBindingResolver,
    BotConcurrencyManager,
    BotRequestWorker,
    BotRequestWorkerConfig,
    BotRunner,
    BotRunRequestExecutor,
    BotServiceConfig,
    BotServiceSelector,
    ClawBotService,
    FixedMachineCountProvider,
    QueueTaskMessageDispatcher,
    SerializingExecutor,
    TaskConcurrencyPool,
    TaskMessageDispatcher,
)
from secbaas.community.core.service.bot_runtime.dispatcher import (
    DefaultBotCmdDispatcher,
    DefaultBotFetchStartProgressDispatcher,
    DefaultBotHttpConnInfoDispatcher,
    DefaultBotHttpDispatcher,
    DefaultBotOpenFolderDispatcher,
    DefaultBotWssDispatcher,
)
from secbaas.community.core.service.bot_session import DefaultSessionService
from secbaas.community.core.service.callback import HttpCallback
from secbaas.community.core.service.config_manage import (
    DefaultSystemConfigManageService,
)
from secbaas.community.core.service.device_binding_query import (
    DeviceBindingQueryService,
)
from secbaas.community.core.service.device_manage import DefaultDeviceService
from secbaas.community.core.service.distributed_lock import DistributedLockService
from secbaas.community.core.service.health_check.bot import BotHealthCheckerService
from secbaas.community.core.service.health_check.paas import PaaSHealthProviderFactory
from secbaas.community.core.service.health_check.sandbox import (
    AcBindingSandboxHandler,
    BaasSandboxHandler,
    SandboxDeviceRouter,
    TableType,
)
from secbaas.community.core.service.paas import (
    PaasServiceFacade,
    PaasServiceFactory,
)
from secbaas.community.core.service.paas.desktop.instance_router import (
    InstanceRouterConfig,
    ThreadSafeLazyRouter,
)
from secbaas.community.core.service.paas.desktop.worker_router import WorkerRouter
from secbaas.community.core.service.publish_manage import (
    DefaultPublishAdminService,
    DefaultPublishService,
)
from secbaas.community.core.service.scheduler import AppScheduler
from secbaas.community.core.service.sse import (
    DefaultStreamConverter,
    SseConverterFactory,
)
from secbaas.community.core.service.template_manage import DefaultDeviceTemplateService
from secbaas.community.core.service.tenant_manage import DefaultTenantManageService
from secbaas.community.spi.sandbox import PaasSandboxPlugins


def _real_bot_service_plugin(base_url: str = "", timeout: float = 10.0):
    from secbaas.community.plugins.bot_service import AiohttpBotServicePlugin

    return AiohttpBotServicePlugin(base_url=base_url, timeout=timeout)


def _stub_bot_service_plugin():
    from secbaas.community.plugins.bot_service import StubBotServicePlugin

    return StubBotServicePlugin()


def _local_bot_service_plugin():
    from secbaas.community.plugins.bot_service import LocalBotServicePlugin

    return LocalBotServicePlugin()


def _create_dispatcher_paas_service(factory: PaasServiceFactory):
    return factory.create_local_paas_service(
        user_id="dispatcher", machine_id="internal-forward"
    )


def _create_system_default_paas_service(factory: PaasServiceFactory):
    return factory.create_local_paas_service(user_id="system", machine_id="default")


def _real_engine_adapter_registry():
    """装配 3 个 real adapter(连真实 engine/proxy 通道)。

    延迟 import:core 层不得 module-level import plugins,故装配在 bootstrap 完成。
    """
    from secbaas.community.core.service.bot_run import BotEngineAdapterRegistry
    from secbaas.community.plugins.bot.engine_adapter.aicoding.real import (
        AICodingAdapter,
    )
    from secbaas.community.plugins.bot.engine_adapter.claude_code.real import (
        ClaudeCodeAdapter,
    )
    from secbaas.community.plugins.bot.engine_adapter.hermes.real import HermesAdapter

    return BotEngineAdapterRegistry(
        {
            "aicoding": AICodingAdapter(),
            "hermes": HermesAdapter(),
            "claude_code": ClaudeCodeAdapter(),
        }
    )


def _stub_engine_adapter_registry():
    """装配 3 个 Noop adapter(安全零值,不连真实 engine;用于测试/本地)。"""
    from secbaas.community.core.service.bot_run import BotEngineAdapterRegistry
    from secbaas.community.plugins.bot.engine_adapter.aicoding.stub import (
        NoopAICodingAdapter,
    )
    from secbaas.community.plugins.bot.engine_adapter.claude_code.stub import (
        NoopClaudeCodeAdapter,
    )
    from secbaas.community.plugins.bot.engine_adapter.hermes.stub import (
        NoopHermesAdapter,
    )

    return BotEngineAdapterRegistry(
        {
            "aicoding": NoopAICodingAdapter(),
            "hermes": NoopHermesAdapter(),
            "claude_code": NoopClaudeCodeAdapter(),
        }
    )


class CoreServiceContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    # Provided by ApplicationContainer (cross-container wiring)
    secret_plugin = providers.Dependency()
    bot_repo = providers.Dependency()
    device_repo = providers.Dependency()
    auth_plugin = providers.Dependency()
    arca_sandbox_plugin_factory = providers.Dependency()
    desktop_sandbox_plugin = providers.Dependency()
    teclaw_bot_plugin_factory = providers.Dependency()
    k8s_sandbox_plugin_factory = providers.Dependency()
    docker_sandbox_plugin = providers.Dependency()
    poolab_sandbox_plugin_factory = providers.Dependency()
    k8s_client_manager = providers.Dependency()
    ac_bot_repo = providers.Dependency()
    ac_bot_publish_repo = providers.Dependency()
    device_binding_repo = providers.Dependency()
    api_gateway_repo = providers.Dependency()
    bot_device_rel_repo = providers.Dependency()
    bot_session_repo = providers.Dependency()
    publish_repo = providers.Dependency()
    publish_batch_repo = providers.Dependency()
    publish_record_repo = providers.Dependency()
    tenant_repo = providers.Dependency()
    system_config_repo = providers.Dependency()
    device_template_repo = providers.Dependency()
    local_user_machine_repo = providers.Dependency()
    bot_run_repository = providers.Dependency()
    bot_run_queue_repository = providers.Dependency()
    bot_run_queue_chunk_repository = providers.Dependency()
    bot_qpm_repository = providers.Dependency()
    distributed_lock_repository = providers.Dependency()
    cache_plugin = providers.Dependency()
    ws_relay_session_repo = providers.Dependency()

    # ── Auth service ──────────────────────────────────────────────────────────

    auth_service = providers.Singleton(
        AuthService,
        plugin=auth_plugin,
    )

    # ── Infrastructure providers ──────────────────────────────────────────────

    system_config_service = providers.Singleton(
        DefaultSystemConfigManageService,
        repository=system_config_repo,
    )

    chat_client_pool = providers.Singleton(
        AsyncChatClientPool,
        max_size=config.chat_client_pool.max_size,
        max_conns_per_sandbox=config.chat_client_pool.max_conns_per_sandbox,
        max_concurrent_per_conn=config.chat_client_pool.max_concurrent_per_conn,
        session_key_timeout=config.chat_client_pool.session_key_timeout,
        max_retries=config.chat_client_pool.max_retries,
        retry_base_backoff=config.chat_client_pool.retry_base_backoff,
        system_config_service=system_config_service,
    )

    tenant_service = providers.Singleton(
        DefaultTenantManageService,
        tenant_repository=tenant_repo,
    )

    device_template_service_infra = providers.Singleton(
        DefaultDeviceTemplateService,
        repository=device_template_repo,
        tenant_service=tenant_service,
        secret_plugin=secret_plugin,
    )

    # ── Desktop infrastructure providers ───────────────────────────────────────────

    connection_management = providers.Dependency()

    worker_router = providers.Singleton(
        WorkerRouter,
        connection_manager=connection_management,
        repository=local_user_machine_repo,
    )

    instance_router_config = providers.Singleton(
        InstanceRouterConfig,
        internal_port=config.web_port,
    )

    instance_router = providers.Singleton(
        ThreadSafeLazyRouter,
        config=instance_router_config,
        local_user_machine_repo=local_user_machine_repo,
    )

    # ── Device plugins ────────────────────────────────────────────────────────

    paas_sandbox_plugins = providers.Singleton(
        PaasSandboxPlugins,
        arca_sandbox_plugin_factory=arca_sandbox_plugin_factory,
        desktop_sandbox_plugin=desktop_sandbox_plugin,
        poolab_sandbox_plugin_factory=poolab_sandbox_plugin_factory,
        teclaw_bot_plugin_factory=teclaw_bot_plugin_factory,
        k8s_sandbox_plugin_factory=k8s_sandbox_plugin_factory,
        docker_sandbox_plugin=docker_sandbox_plugin,
    )

    paas_service_factory = providers.Singleton(
        PaasServiceFactory,
        template_service=device_template_service_infra,
        connection_manager=connection_management,
        worker_router=worker_router,
        instance_router=instance_router,
        device_template_repository=device_template_repo,
        device_repository=device_repo,
        publish_record_repository=publish_record_repo,
        local_user_machine_repository=local_user_machine_repo,
        paas_sandbox_plugins=paas_sandbox_plugins,
        secret_plugin=secret_plugin,
        ws_relay_session_repository=ws_relay_session_repo,
    )

    paas_facade = providers.Singleton(
        PaasServiceFacade,
        device_repository=device_repo,
        device_template_service=device_template_service_infra,
        factory=paas_service_factory,
    )

    dispatcher_local_paas_service = providers.Singleton(
        _create_dispatcher_paas_service,
        factory=paas_service_factory,
    )

    system_default_local_paas_service = providers.Singleton(
        _create_system_default_paas_service,
        factory=paas_service_factory,
    )

    bot_service_config = providers.Singleton(
        BotServiceConfig,
        proxy_base_url=config.bot_service.proxy_base_url,
        proxy_ws_base_url=config.bot_service.proxy_ws_base_url,
        adapter_port=config.bot_service.adapter_port,
        connect_timeout=config.bot_service.connect_timeout,
        request_timeout=config.bot_service.request_timeout,
    )

    baas_bot_service_config = providers.Singleton(
        BaasBotServiceConfig,
        adapter_port=config.bot_service.adapter_port,
        ws_path=config.bot_service.ws_path,
        connect_timeout=config.bot_service.connect_timeout,
        request_timeout=config.bot_service.request_timeout,
    )

    bot_wss_dispatcher = providers.Singleton(
        DefaultBotWssDispatcher,
        bot_repo=bot_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
    )

    bot_http_conn_info_dispatcher = providers.Singleton(
        DefaultBotHttpConnInfoDispatcher,
        bot_repo=bot_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
    )

    bot_http_dispatcher = providers.Singleton(
        DefaultBotHttpDispatcher,
        bot_repo=bot_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
    )

    bot_cmd_dispatcher = providers.Singleton(
        DefaultBotCmdDispatcher,
        bot_repo=bot_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
    )

    bot_open_folder_dispatcher = providers.Singleton(
        DefaultBotOpenFolderDispatcher,
        bot_repo=bot_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
    )

    bot_fetch_start_progress_dispatcher = providers.Singleton(
        DefaultBotFetchStartProgressDispatcher,
        bot_repo=bot_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
    )

    bot_binding_resolver = providers.Singleton(
        BotBindingResolver,
        ac_bot_repo=ac_bot_repo,
        publish_repo=ac_bot_publish_repo,
        binding_repo=device_binding_repo,
    )

    # Engine adapter registry — 按 config.plugins.engine_adapter 切 stub/real,
    # 注入 BaasBotService 和 ClawBotService。stub=Noop(测试/本地),real=连真实 engine 通道。
    engine_adapter_registry = providers.Selector(
        config.plugins.engine_adapter,
        real=providers.Singleton(_real_engine_adapter_registry),
        stub=providers.Singleton(_stub_engine_adapter_registry),
    )

    claw_bot_service = providers.Singleton(
        ClawBotService,
        config=bot_service_config,
        client_pool=chat_client_pool,
        secret_store=secret_plugin,
        engine_adapter_registry=engine_adapter_registry,
    )

    # ── Service providers ─────────────────────────────────────────────────────

    tenant_service = providers.Singleton(
        DefaultTenantManageService,
        tenant_repository=tenant_repo,
    )

    device_template_service = providers.Singleton(
        DefaultDeviceTemplateService,
        repository=device_template_repo,
        tenant_service=tenant_service,
        secret_plugin=secret_plugin,
    )

    # ── Bot Health Checker service ────────────────────────────────────────────

    bot_health_checker_config = providers.Singleton(
        BotHealthCheckerConfig,
        health_check_timeout=config.bot_health_checker.health_check.timeout_seconds,
        health_check_max_concurrent=config.bot_health_checker.health_check.max_concurrent,
        extend_when_remaining_hours=config.bot_health_checker.ttl.extend_when_remaining_hours,
        target_ttl_hours=config.bot_health_checker.ttl.target_ttl_hours,
    )

    paas_health_provider_factory = providers.Singleton(
        PaaSHealthProviderFactory,
        paas_facade=paas_facade,
        timeout_seconds=config.bot_health_checker.health_check.timeout_seconds,
        k8s_client_manager=k8s_client_manager,
    )

    device_binding_query_service = providers.Singleton(
        DeviceBindingQueryService,
        ac_bot_repo=ac_bot_repo,
        ac_bot_publish_repo=ac_bot_publish_repo,
        binding_repo=device_binding_repo,
        bot_repo=bot_repo,
        device_repo=device_repo,
    )

    bot_health_checker_service = providers.Singleton(
        BotHealthCheckerService,
        device_binding_repo=device_binding_repo,
        device_repo=device_repo,
        paas_facade=paas_facade,
        config=bot_health_checker_config,
        health_provider_factory=paas_health_provider_factory,
        query_service=device_binding_query_service,
    )

    device_service = providers.Singleton(
        DefaultDeviceService,
        paas_facade=paas_facade,
        repository=device_repo,
        device_template_service=device_template_service,
        secret_plugin=secret_plugin,
    )

    session_service = providers.Singleton(
        DefaultSessionService,
        repository=bot_session_repo,
    )

    baas_bot_service = providers.Singleton(
        BaasBotService,
        client_pool=chat_client_pool,
        config=baas_bot_service_config,
        wss_resolver=bot_wss_dispatcher,
        session_service=session_service,
        engine_adapter_registry=engine_adapter_registry,
    )

    bot_qpm_manage_service = providers.Singleton(
        DefaultBotQpmManageService,
        repository=bot_qpm_repository,
    )

    api_key_service = providers.Singleton(
        DefaultAPIKeyService,
        repository=api_gateway_repo,
    )
    api_key_validator = providers.Singleton(
        DefaultAPIKeyValidator,
        repository=api_gateway_repo,
    )

    bot_crud_service = providers.Singleton(
        DefaultBotCrudService,
        bot_repo=bot_repo,
        device_repo=device_repo,
        rel_repo=bot_device_rel_repo,
        device_template_service=device_template_service,
        device_service=device_service,
    )

    publish_service = providers.Singleton(
        DefaultPublishService,
        bot_repo=bot_repo,
        device_repo=device_repo,
        rel_repo=bot_device_rel_repo,
        session_repo=bot_session_repo,
        publish_repo=publish_repo,
        batch_repo=publish_batch_repo,
        publish_record_repo=publish_record_repo,
        template_service=device_template_service,
        bot_service=bot_crud_service,
        device_service=device_service,
    )

    bot_management_service = providers.Singleton(
        DefaultBotManagementService,
        bot_repo=bot_repo,
        device_repo=device_repo,
        system_config_repo=system_config_repo,
        publish_service=publish_service,
        bot_service=bot_crud_service,
        health_checker=bot_health_checker_service,
    )

    publish_admin_service = providers.Singleton(
        DefaultPublishAdminService,
        publish_repo=publish_repo,
        batch_repo=publish_batch_repo,
        record_repo=publish_record_repo,
        device_repo=device_repo,
        bot_repo=bot_repo,
    )

    bot_service_selector = providers.Singleton(
        BotServiceSelector,
        claw_service=claw_bot_service,
        baas_service=baas_bot_service,
    )

    task_concurrency_pool = providers.Singleton(
        TaskConcurrencyPool,
        softmax=config.bot_runner.softmax,
        per_key_max=config.bot_runner.per_key_max,
        queue_max=config.bot_runner.queue_max,
        acquire_timeout=config.bot_runner.acquire_timeout,
        task_timeout=config.bot_runner.task_timeout,
    )

    bcn_uplink_config = providers.Singleton(
        BcnUplinkConfig,
        base_url=config.bcn.uplink.base_url,
        provider_id=config.bcn.uplink.provider_id,
    )

    bcn_uplink_client = providers.Singleton(
        BcnUplinkClient,
        config=bcn_uplink_config,
        secret_plugin=secret_plugin,
    )

    bcn_uplink_callback = providers.Singleton(
        BcnUplinkCallback,
        uplink_client=bcn_uplink_client,
        run_repository=bot_run_repository,
    )

    # ── BotService plugin ────────────────────────────────────────────────────

    bot_service_plugin = providers.Selector(
        config.plugins.bot_service,
        real=providers.Singleton(
            _real_bot_service_plugin,
            base_url=config.bot_chat_log_relation.base_url,
            timeout=config.bot_chat_log_relation.timeout,
        ),
        local=providers.Singleton(_local_bot_service_plugin),
        stub=providers.Singleton(_stub_bot_service_plugin),
    )

    http_callback = providers.Singleton(
        HttpCallback,
        run_repository=bot_run_repository,
    )

    task_message_dispatcher = providers.Singleton(
        TaskMessageDispatcher,
        run_repository=bot_run_repository,
        task_pool=task_concurrency_pool,
        post_run_callback_factories=providers.Dict(
            {
                "bcn_uplink": bcn_uplink_callback,
                "http_callback": http_callback,
            }
        ),
    )

    # ── BotRun queue worker providers ─────────────────────────────────────────

    distributed_lock_service = providers.Singleton(
        DistributedLockService,
        repository=distributed_lock_repository,
        default_expire_seconds=config.bot_run_queue.session_lock_expire_seconds,
        renew_interval_seconds=config.bot_run_queue.session_lock_renew_seconds,
    )

    bot_qpm_manager = providers.Singleton(
        BotConcurrencyManager,
        repository=bot_qpm_repository,
        refresh_interval_seconds=config.bot_run_queue.qpm_refresh_seconds,
    )

    queue_task_message_dispatcher = providers.Singleton(
        QueueTaskMessageDispatcher,
        run_repository=bot_run_repository,
        queue_repository=bot_run_queue_repository,
        chunk_repository=bot_run_queue_chunk_repository,
        cache_plugin=cache_plugin,
        system_config_service=system_config_service,
    )

    bot_runner = providers.Singleton(
        BotRunner,
        bot_service_selector=bot_service_selector,
        run_repository=bot_run_repository,
        bot_service_plugin=bot_service_plugin,
        dispatchers=providers.List(
            queue_task_message_dispatcher,
            task_message_dispatcher,
        ),
        system_config_service=system_config_service,
    )

    bcn_downlink_service = providers.Singleton(
        DefaultBcnDownlinkService,
        bot_runner=bot_runner,
        api_key_repository=api_gateway_repo,
        bcn_api_key_prefix=config.bcn.api_key.prefix,
        uplink_client=bcn_uplink_client,
        run_repository=bot_run_repository,
    )

    # ── SSE stream converter factory ────────────────────────────────────────
    stream_converter_factory = providers.Singleton(
        SseConverterFactory,
        converter_factories=providers.Dict(
            {
                "default": providers.Object(DefaultStreamConverter),
            }
        ),
    )

    bot_run_request_executor = providers.Singleton(
        BotRunRequestExecutor,
        run_repository=bot_run_repository,
        bot_service_plugin=bot_service_plugin,
        bot_service_selector=bot_service_selector,
        chunk_repository=bot_run_queue_chunk_repository,
        cache_plugin=cache_plugin,
        api_key_repository=api_gateway_repo,
    )

    serializing_executor = providers.Singleton(
        SerializingExecutor,
        inner=bot_run_request_executor,
        lock_service=distributed_lock_service,
        queue_repository=bot_run_queue_repository,
    )

    bot_request_worker_config = providers.Singleton(
        BotRequestWorkerConfig,
        enabled=config.bot_run_queue.enabled,
        poll_interval_seconds=config.bot_run_queue.poll_interval_seconds,
        discover_limit=config.bot_run_queue.discover_limit,
        candidates_per_bot=config.bot_run_queue.candidates_per_bot,
        max_concurrent=config.bot_run_queue.max_concurrent,
        heartbeat_interval_seconds=config.bot_run_queue.heartbeat_interval_seconds,
        bucket_sweep_interval_seconds=config.bot_run_queue.bucket_sweep_interval_seconds,
        bucket_idle_ttl_seconds=config.bot_run_queue.bucket_idle_ttl_seconds,
    )

    machine_count_provider = providers.Singleton(
        FixedMachineCountProvider,
        count=config.bot_run_queue.machine_count,
    )

    bot_request_worker = providers.Singleton(
        BotRequestWorker,
        queue_repository=bot_run_queue_repository,
        run_repository=bot_run_repository,
        qpm_manager=bot_qpm_manager,
        executor=serializing_executor,
        post_run_callback_factories=providers.Dict(
            {
                "bcn_uplink": bcn_uplink_callback,
                "http_callback": http_callback,
            }
        ),
        machine_count_provider=machine_count_provider,
        config=bot_request_worker_config,
    )

    # ── Sandbox device management providers ───────────────────────────────────

    ac_binding_sandbox_handler = providers.Singleton(
        AcBindingSandboxHandler,
        binding_repo=device_binding_repo,
        paas_facade=paas_facade,
    )

    baas_sandbox_handler = providers.Singleton(
        BaasSandboxHandler,
        binding_repo=device_binding_repo,
        paas_facade=paas_facade,
    )

    sandbox_device_router = providers.Singleton(
        SandboxDeviceRouter,
        handlers=providers.Dict(
            {
                TableType.AC_BINDING: ac_binding_sandbox_handler,
                TableType.BAAS: baas_sandbox_handler,
            }
        ),
    )

    # ── AppScheduler ─────────────────────────────────────────────────────────

    app_scheduler = providers.Singleton(
        AppScheduler,
    )
