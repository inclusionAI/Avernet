"""Runtime policy for dormant-bot scheduled scans.

The policy is stored in ``ac_common_config`` so pre/prod can be controlled
without code changes:

``business_code=bot_dormant, param_code=scan_policy, env=<current_env>``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from injector import inject

from agentclaw.community.core.common_config import CommonConfigService
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()

BUSINESS_CODE = "bot_dormant"
PARAM_CODE = "scan_policy"
DEFAULT_INACTIVE_THRESHOLD_DAYS = 7
DEFAULT_RECYCLE_GRACE_DAYS = 3


def positive_int_or_default(value: Any, default: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    if not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return parsed


@dataclass(frozen=True)
class DormantScanPolicy:
    """Effective runtime policy for the dormant scheduled scan."""

    scheduled_scan_enabled: bool
    dry_run: bool
    source: str
    env: str
    inactive_threshold_days: int = DEFAULT_INACTIVE_THRESHOLD_DAYS
    recycle_grace_days: int = DEFAULT_RECYCLE_GRACE_DAYS


class DormantScanPolicyService:
    """Reads dormant scan switches from ``ac_common_config``.

    Missing config falls back to the legacy-safe behavior requested by ops:
    prod starts the 03:00 scan in dry-run mode; non-prod does not start scans.
    Disabled rows explicitly disable scheduled scans for that env.
    """

    @inject
    def __init__(self, common_config_service: CommonConfigService) -> None:
        self._common_config_service = common_config_service

    @staticmethod
    def _fallback(env: str, source: str = "fallback_missing") -> DormantScanPolicy:
        return DormantScanPolicy(
            scheduled_scan_enabled=(env == "prod"),
            dry_run=True,
            source=source,
            env=env,
        )

    @staticmethod
    def _as_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return default

    def get_policy(self) -> DormantScanPolicy:
        env = get_current_env()
        try:
            config = self._common_config_service.get_config(
                business_code=BUSINESS_CODE,
                param_code=PARAM_CODE,
                env=env,
                only_enabled=False,
            )
        except Exception:
            logger.exception(
                "[dormant.scan_policy] failed to read ac_common_config, using fallback env=%s",
                env,
            )
            return self._fallback(env, source="fallback_error")

        if config is None:
            return self._fallback(env)

        if str(config.get("enable")) != "1":
            return DormantScanPolicy(
                scheduled_scan_enabled=False,
                dry_run=True,
                source="common_config_disabled",
                env=env,
            )

        raw_value = config.get("param_value")
        if not isinstance(raw_value, dict):
            logger.warning(
                "[dormant.scan_policy] invalid param_value type env=%s value=%r, using fallback",
                env,
                raw_value,
            )
            return self._fallback(env, source="fallback_invalid")

        fallback = self._fallback(env, source="fallback_partial")
        return DormantScanPolicy(
            scheduled_scan_enabled=self._as_bool(
                raw_value.get("scheduled_scan_enabled"),
                default=fallback.scheduled_scan_enabled,
            ),
            dry_run=self._as_bool(raw_value.get("dry_run"), default=True),
            source="common_config",
            env=env,
            inactive_threshold_days=positive_int_or_default(
                raw_value.get("inactive_threshold_days"),
                DEFAULT_INACTIVE_THRESHOLD_DAYS,
            ),
            recycle_grace_days=positive_int_or_default(
                raw_value.get("recycle_grace_days"),
                DEFAULT_RECYCLE_GRACE_DAYS,
            ),
        )

    def scheduled_scan_enabled(self) -> bool:
        return self.get_policy().scheduled_scan_enabled

    def dry_run(self) -> bool:
        return self.get_policy().dry_run

    def inactive_threshold_days(self) -> int:
        return self.get_policy().inactive_threshold_days

    def recycle_grace_days(self) -> int:
        return self.get_policy().recycle_grace_days
