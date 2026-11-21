"""Runtime policy for dormant-bot auto-reactivation after recycle.

The policy is stored in ``ac_common_config`` so pre/prod can be controlled
without code changes:

``business_code=bot_dormant, param_code=auto_reactivate_policy, env=<current_env>``.

Safe defaults: enabled=False, dry_run=True — no auto-activation happens
until the policy is explicitly enabled in pre/prod configuration.
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
PARAM_CODE = "auto_reactivate_policy"


@dataclass(frozen=True)
class AutoReactivatePolicy:
    """Effective runtime policy for auto-reactivation after dormant recycle."""

    auto_reactivate_enabled: bool
    dry_run: bool
    source: str
    env: str


class AutoReactivatePolicyService:
    """Reads auto-reactivate switches from ``ac_common_config``.

    Missing config falls back to safe defaults: ``enabled=False, dry_run=True``.
    This guarantees that no auto-activation happens without explicit operator
    intent in every environment.
    """

    @inject
    def __init__(self, common_config_service: CommonConfigService) -> None:
        self._common_config_service = common_config_service

    @staticmethod
    def _fallback(env: str, source: str = "fallback_missing") -> AutoReactivatePolicy:
        return AutoReactivatePolicy(
            auto_reactivate_enabled=False,
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

    def get_policy(self) -> AutoReactivatePolicy:
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
                "[dormant.auto_reactivate_policy] failed to read ac_common_config, "
                "using fallback env=%s",
                env,
            )
            return self._fallback(env, source="fallback_error")

        if config is None:
            return self._fallback(env)

        if str(config.get("enable")) != "1":
            return AutoReactivatePolicy(
                auto_reactivate_enabled=False,
                dry_run=True,
                source="common_config_disabled",
                env=env,
            )

        raw_value = config.get("param_value")
        if not isinstance(raw_value, dict):
            logger.warning(
                "[dormant.auto_reactivate_policy] invalid param_value type "
                "env=%s value=%r, using fallback",
                env,
                raw_value,
            )
            return self._fallback(env, source="fallback_invalid")

        fallback = self._fallback(env, source="fallback_partial")
        return AutoReactivatePolicy(
            auto_reactivate_enabled=self._as_bool(
                raw_value.get("auto_reactivate_enabled"),
                default=fallback.auto_reactivate_enabled,
            ),
            dry_run=self._as_bool(
                raw_value.get("dry_run"), default=True
            ),
            source="common_config",
            env=env,
        )

    def auto_reactivate_enabled(self) -> bool:
        return self.get_policy().auto_reactivate_enabled

    def dry_run(self) -> bool:
        return self.get_policy().dry_run
