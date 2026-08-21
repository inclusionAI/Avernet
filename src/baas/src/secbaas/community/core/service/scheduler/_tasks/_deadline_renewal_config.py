"""Deadline renewal scheduler configuration dataclass.

All fields match design doc §8.1 plus `enabled`, `lock_name`, and `engine`
fields required by the DI container and config switch.
"""

from __future__ import annotations

from dataclasses import dataclass

from secbaas.community.core.utils.env_utils import get_current_env


@dataclass
class DeadlineRenewalSchedulerConfig:
    """Configuration for the DeadlineRenewalScheduler.

    Fields:
        enabled: Whether the scheduler is active (derived from engine="deadline").
        lock_name: Distributed lock name (unique per scheduler).
        lock_expire_seconds: Lock lease duration in seconds.
        cron_interval_seconds: Cron trigger interval in seconds.
        batch_size: Maximum due records to process per run.
        max_concurrency: Semaphore limit for concurrent renewal calls.
        renew_threshold_hours: Only renew when remaining TTL ≤ this threshold.
        retry_delay_minutes: Delay between renewal retry attempts.
        max_fail_count: Consecutive failure count before marking STOPPED.
        ttl_safety_margin_minutes: Safety buffer subtracted from ttl_minutes.
        anti_join_verify_interval_cycles: Periodic anti-join verification interval.
        engine: Config switch — "legacy" or "deadline".
        env: Deployment environment identifier ('pre', 'prod', or '' for test).
            Sourced from ALIPAY_APP_ENV or DEPLOY_ENV via get_current_env().
            Injected by DI wiring in _core_tasks.py.
    """

    enabled: bool = False
    lock_name: str = "deadline_renewal_scheduler_lock"
    lock_expire_seconds: int = 1800
    cron_interval_seconds: int = 1800
    batch_size: int = 500
    max_concurrency: int = 20
    renew_threshold_hours: int = 12
    retry_delay_minutes: int = 2
    max_fail_count: int = 10
    ttl_safety_margin_minutes: int = 1
    anti_join_verify_interval_cycles: int = 48
    engine: str = "legacy"
    env: str = ""

    def resolved_lock_name(self) -> str:
        """Return the env-scoped distributed lock name.

        Appends the environment suffix (e.g. ``_pre``/``_prod``) to the
        configured ``lock_name`` so pre and prod use different locks —
        pre/prod share one MySQL instance (and therefore one distributed
        lock table); a fixed lock name would make the two environments'
        schedulers take turns. Mirrors DeviceTtlTimerTaskConfig.
        """
        env = get_current_env()
        return f"{self.lock_name}_{env}"