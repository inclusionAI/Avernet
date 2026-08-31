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
    ExpireSandboxTimerTask,
    ExpireSandboxTimerTaskConfig,
    FileTransferPoller,
    FileTransferPollerConfig,
)
from secbaas.community.core.utils.env_utils import get_current_env


def _assert_threshold_consistent(thr, ttl) -> int:
    """Coerce renew_threshold_hours and fail fast on EG-4 drift.

    None-tolerant for the threshold itself: when the YAML key is absent
    (None/empty), the 12-hour default is returned without asserting —
    minimal test containers carry only the engine key, and odd half-TTL
    deployments must omit the key and rely on the derived value. Whenever
    the threshold IS explicitly present, it is asserted against the
    effective TTL period — arca.default_ttl_minutes when set, otherwise the
    1440-minute fallback — so a tuned threshold can never revert silently
    (WR-02). A threshold that is not half the effective TTL period raises
    ValueError at assembly (startup failure instead of a silently drifted
    safety margin).
    """
    coerced = int(thr) if thr else 12
    effective_ttl = int(ttl) if ttl else 1440
    if thr and coerced * 60 != effective_ttl // 2:
        raise ValueError(
            f"renew_threshold_hours={thr!r} conflicts with "
            f"arca.default_ttl_minutes={ttl!r} (effective {effective_ttl}) — "
            "threshold must equal half the TTL period (EG-4)"
        )
    return coerced


class CoreTaskContainer(containers.DeclarativeContainer):
    config = providers.Configuration()

    # Provided by ApplicationContainer (cross-container wiring)
    distributed_lock_service = providers.Dependency()
    device_repo = providers.Dependency()
    device_binding_repo = providers.Dependency()
    sandbox_device_router = providers.Dependency()
    bot_run_queue_repository = providers.Dependency()
    ticket_repository = providers.Dependency()
    paas_service_facade = providers.Dependency()
    file_transfer_backend = providers.Dependency()
    ttl_renewal_schedule_repository = providers.Dependency()
    device_service = providers.Dependency()
    bot_manage_service = providers.Dependency()
    bot_repo = providers.Dependency()
    bot_device_rel_repo = providers.Dependency()
    arca_ttl_schedule_repository = providers.Dependency()
    system_config_service = providers.Dependency()

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
        # EG-4 fail-fast: the explicit threshold must equal half the TTL
        # period (None-tolerant — a missing key returns 12, no assertion).
        renew_threshold_hours=providers.Callable(
            _assert_threshold_consistent,
            config.renewal_scheduler.renew_threshold_hours,
            config.arca.default_ttl_minutes,
        ),
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
        # D-01 tolerance knob wired symmetrically with default_ttl_minutes:
        # the renewal_scheduler schema allows undeclared keys
        # (SettingsConfigDict(extra="allow")), so a YAML-set
        # post_extend_consistency_tol_minutes passes validation and must
        # reach the watermark comparison — otherwise the operator believes
        # the tolerance tightened while it silently stays 5 (WR-01).
        post_extend_consistency_tol_minutes=providers.Callable(
            lambda v: int(v) if v else 5,
            config.renewal_scheduler.post_extend_consistency_tol_minutes,
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

    # ── ExpireSandboxTimer task ───────────────────────────────────────────────

    expire_sandbox_timer_config = providers.Singleton(
        ExpireSandboxTimerTaskConfig,
        enabled=config.expire_sandbox_timer.enabled,
        arca_provider=config.plugins.sandbox.arca,
        lock_name=config.expire_sandbox_timer.lock_name,
        lock_expire_seconds=config.expire_sandbox_timer.lock_expire_seconds,
        cron_interval_seconds=config.expire_sandbox_timer.cron_interval_seconds,
        batch_size=config.expire_sandbox_timer.batch_size,
        max_page_concurrency=config.expire_sandbox_timer.max_page_concurrency,
        query_retries=config.expire_sandbox_timer.query_retries,
        dry_run=config.expire_sandbox_timer.dry_run,
        grace_seconds=config.expire_sandbox_timer.grace_seconds,
        default_ttl_minutes=config.expire_sandbox_timer.default_ttl_minutes,
    )

    expire_sandbox_timer_task = providers.Singleton(
        ExpireSandboxTimerTask,
        config=expire_sandbox_timer_config,
        lock_service=distributed_lock_service,
        device_repo=device_repo,
        bot_manage_service=bot_manage_service,
        bot_repo=bot_repo,
        bot_device_rel_repo=bot_device_rel_repo,
        system_config_service=system_config_service,
    )
