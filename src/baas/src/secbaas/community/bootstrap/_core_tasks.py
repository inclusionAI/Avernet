"""Core task container — 定时 task 的 DI 注册

参考 _core_services.py 的模式，将定时 task 及其配置注册为容器 bean，
依赖通过构造方法注入。
"""

from dependency_injector import containers, providers

from secbaas.community.core.service.scheduler import (
    BotRunRecoveryTask,
    BotRunRecoveryTaskConfig,
    DeadlineRenewalScheduler,
    DeadlineRenewalSchedulerConfig,
    DeviceTtlTimerTask,
    DeviceTtlTimerTaskConfig,
    FileTransferPoller,
    FileTransferPollerConfig,
)
from secbaas.community.core.utils.env_utils import get_current_env


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
    arca_ttl_schedule_repository = providers.Dependency()

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

    # ── DeadlineRenewal scheduler ──────────────────────────────────────────

    deadline_renewal_config = providers.Singleton(
        DeadlineRenewalSchedulerConfig,
        enabled=providers.Callable(
            lambda e: e == "deadline",
            config.renewal_scheduler.engine,
        ),
        lock_name=config.renewal_scheduler.lock_name,
        lock_expire_seconds=config.renewal_scheduler.lock_expire_seconds,
        cron_interval_seconds=config.renewal_scheduler.cron_interval_seconds,
        batch_size=config.renewal_scheduler.batch_size,
        max_concurrency=config.renewal_scheduler.max_concurrency,
        renew_threshold_hours=config.renewal_scheduler.renew_threshold_hours,
        # Rule 14 (configuration-driven wiring): the TTL period comes from
        # the arca config section, not hardcoded task constants. The
        # fallback keeps overlays without an arca section (minimal test
        # containers) on the 1440 default. WR-03: coerce to int — the
        # value has no ArcaConfigSchema, so a quoted YAML number ("1440")
        # would reach the scheduler as str and raise TypeError at the
        # first cron run (default_ttl_minutes // 2).
        default_ttl_minutes=providers.Callable(
            lambda v: int(v) if v else 1440,
            config.arca.default_ttl_minutes,
        ),
        retry_delay_minutes=config.renewal_scheduler.retry_delay_minutes,
        max_fail_count=config.renewal_scheduler.max_fail_count,
        ttl_safety_margin_minutes=config.renewal_scheduler.ttl_safety_margin_minutes,
        anti_join_verify_interval_cycles=config.renewal_scheduler.anti_join_verify_interval_cycles,
        engine=config.renewal_scheduler.engine,
        env=providers.Callable(get_current_env),
    )

    deadline_renewal_task = providers.Singleton(
        DeadlineRenewalScheduler,
        config=deadline_renewal_config,
        lock_service=distributed_lock_service,
        schedule_repo=arca_ttl_schedule_repository,
        paas_facade=paas_service_facade,
    )
