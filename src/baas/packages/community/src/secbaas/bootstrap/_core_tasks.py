"""Core task container — 定时 task 的 DI 注册

参考 _core_services.py 的模式，将定时 task 及其配置注册为容器 bean，
依赖通过构造方法注入。
"""

from dependency_injector import containers, providers

from secbaas.core.service.scheduler import (
    BotRunRecoveryTask,
    BotRunRecoveryTaskConfig,
    DeviceTtlTimerTask,
    DeviceTtlTimerTaskConfig,
)


class CoreTaskContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    # Provided by ApplicationContainer (cross-container wiring)
    distributed_lock_service = providers.Dependency()
    device_binding_repo = providers.Dependency()
    sandbox_device_router = providers.Dependency()
    bot_run_queue_repository = providers.Dependency()

    # ── DeviceTtlTimer task ──────────────────────────────────────────────────

    device_ttl_timer_config = providers.Singleton(
        DeviceTtlTimerTaskConfig,
        enabled=config.device_ttl_timer.enabled,
        lock_name=config.device_ttl_timer.lock_name,
        lock_expire_seconds=config.device_ttl_timer.lock_expire_seconds,
        cron_interval_seconds=config.device_ttl_timer.cron_interval_seconds,
        batch_size=config.device_ttl_timer.batch_size,
        dry_run=config.device_ttl_timer.dry_run,
    )

    device_ttl_timer_task = providers.Singleton(
        DeviceTtlTimerTask,
        config=device_ttl_timer_config,
        lock_service=distributed_lock_service,
        binding_repo=device_binding_repo,
        router=sandbox_device_router,
    )

    # ── BotRunRecovery task ──────────────────────────────────────────────────

    bot_run_recovery_config = providers.Singleton(
        BotRunRecoveryTaskConfig,
        enabled=config.bot_run_recovery.enabled,
        lock_name=config.bot_run_recovery.lock_name,
        lock_expire_seconds=config.bot_run_recovery.lock_expire_seconds,
        cron_interval_seconds=config.bot_run_recovery.cron_interval_seconds,
        stale_seconds=config.bot_run_recovery.stale_seconds,
        dry_run=config.bot_run_recovery.dry_run,
    )

    bot_run_recovery_task = providers.Singleton(
        BotRunRecoveryTask,
        config=bot_run_recovery_config,
        lock_service=distributed_lock_service,
        queue_repo=bot_run_queue_repository,
    )
