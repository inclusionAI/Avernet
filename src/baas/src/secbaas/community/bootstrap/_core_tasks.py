"""Core task container — 定时 task 的 DI 注册

参考 _core_services.py 的模式，将定时 task 及其配置注册为容器 bean，
依赖通过构造方法注入。
"""

from dependency_injector import containers, providers

from secbaas.community.core.service.scheduler import (
    BotRunRecoveryTask,
    BotRunRecoveryTaskConfig,
    DeviceTtlTimerTask,
    DeviceTtlTimerTaskConfig,
    FileTransferPoller,
    FileTransferPollerConfig,
)
from secbaas.community.core.utils.env_utils import get_current_env

# ── Enterprise-only optional imports ─────────────────────────────────────
# DeadlineRenewalScheduler lives in the enterprise flat tree. Community
# builds that don't have the enterprise package on PYTHONPATH will see
# _HAS_ENTERPRISE_RENEWAL = False and skip the deadline path.
try:
    from secbaas.enterprise.core.arca_ttl_renewal import (
        DeadlineRenewalScheduler,
        DeadlineRenewalSchedulerConfig,
        TtlRenewalScheduleRepository,
    )

    _HAS_ENTERPRISE_RENEWAL = True
except ImportError:
    _HAS_ENTERPRISE_RENEWAL = False


class CoreTaskContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    # Provided by ApplicationContainer (cross-container wiring)
    distributed_lock_service = providers.Dependency()
    device_binding_repo = providers.Dependency()
    sandbox_device_router = providers.Dependency()
    bot_run_queue_repository = providers.Dependency()
    ticket_repository = providers.Dependency()
    paas_service_facade = providers.Dependency()
    file_transfer_backend = providers.Dependency()
    ttl_renewal_schedule_repository = providers.Dependency()

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

    # ── FileTransferPoller task ────────────────────────────────────────────────

    file_transfer_poller_config = providers.Singleton(
        FileTransferPollerConfig,
        enabled=config.file_transfer_poller.enabled,
        lock_expire_seconds=config.file_transfer_poller.lock_expire_seconds,
        cron_interval_seconds=config.file_transfer_poller.cron_interval_seconds,
        upload_timeout_seconds=config.file_transfer_poller.upload_timeout_seconds,
        max_concurrent_tickets=config.file_transfer_poller.max_concurrent_tickets,
        dry_run=config.file_transfer_poller.dry_run,
    )

    file_transfer_poller_task = providers.Singleton(
        FileTransferPoller,
        config=file_transfer_poller_config,
        lock_service=distributed_lock_service,
        ticket_repo=ticket_repository,
        file_backend=file_transfer_backend,
        paas_facade=paas_service_facade,
    )

    # ── DeadlineRenewalScheduler (enterprise-only, conditional) ────────────
    # Per D-04: if/else branch (not Plugin Selector). The enabled flag is
    # derived from config.renewal_scheduler.engine: only True when "deadline".
    # When enterprise is not installed, deadline_renewal_scheduler is None.
    if _HAS_ENTERPRISE_RENEWAL:
        deadline_renewal_config = providers.Singleton(
            DeadlineRenewalSchedulerConfig,
            enabled=providers.Callable(
                lambda engine: engine == "deadline",
                config.renewal_scheduler.engine,
            ),
            engine=config.renewal_scheduler.engine,
            env=providers.Callable(get_current_env),
        )

        deadline_renewal_scheduler = providers.Singleton(
            DeadlineRenewalScheduler,
            config=deadline_renewal_config,
            lock_service=distributed_lock_service,
            schedule_repo=ttl_renewal_schedule_repository,
            paas_facade=paas_service_facade,
        )
    else:
        deadline_renewal_scheduler = providers.Object(None)
