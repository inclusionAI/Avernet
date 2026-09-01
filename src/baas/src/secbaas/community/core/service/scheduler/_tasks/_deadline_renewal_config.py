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
        default_ttl_minutes: Configured ARCA TTL period (from arca.default_ttl_minutes,
            injected by DI). The renewal lead window is derived as half of it;
            the ttl_minutes formula in Step 3(h) is normalized to this period.
        retry_delay_minutes: Delay between renewal retry attempts.
        max_fail_count: Consecutive failure count before marking STOPPED.
        ttl_safety_margin_minutes: Safety buffer subtracted from ttl_minutes.
        post_extend_consistency_tol_minutes: Upper-bound tolerance for the
            post-extend TTL consistency watermark (D1).
        clock_tol_minutes: Host-clock grace margin for the threshold_expired
            verdict (WR-02). Only a remaining below -(clock_tol_minutes/60)
            hours confirms expiry; readings within +/-clock_tol_minutes of
            zero route through non-confirming failure handling. Non-negative;
            0 disables the margin.
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
    default_ttl_minutes: int = 1440
    retry_delay_minutes: int = 2
    max_fail_count: int = 10
    ttl_safety_margin_minutes: int = 1
    anti_join_verify_interval_cycles: int = 48
    post_extend_consistency_tol_minutes: int = 5
    # WR-02 (86-REVIEW, option 1): host-clock grace margin for the
    # threshold_expired verdict — see the class docstring. 0 disables it.
    clock_tol_minutes: int = 5
    engine: str = "legacy"
    env: str = ""

    def __post_init__(self) -> None:
        """Reject a negative grace margin (WR-02).

        A negative clock_tol_minutes would move the expired threshold into
        positive remaining time and let a skewed host clock confirm live
        containers as expired. The DI path validates the value earlier via
        the renewal_scheduler schema (ge=0); this guard covers direct
        construction (tests, callers outside the DI container).
        """
        if self.clock_tol_minutes < 0:
            raise ValueError(
                "clock_tol_minutes must be non-negative, "
                f"got {self.clock_tol_minutes}"
            )

    @property
    def renew_threshold_minutes(self) -> int:
        """Renewal threshold in minutes — half the configured TTL period.

        EG-4 single-source: the (g)/(h) decision derives from
        default_ttl_minutes instead of the YAML renew_threshold_hours, so a
        reconfigured TTL period keeps the threshold coherent. Minutes
        granularity preserves odd half-periods (e.g. 1500//2 = 750) that
        the hours field cannot express.
        """
        return self.default_ttl_minutes // 2

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
