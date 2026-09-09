"""Runtime-configurable Installation Reader migration gate."""

from __future__ import annotations

from typing import Any

from agentclaw.community.core.common_config.common_config_service_protocol import (
    CommonConfigServiceProtocol,
)
from agentclaw.community.log import get_logger


logger = get_logger()

INSTALLATION_READ_BUSINESS_CODE = "skill_installation"
INSTALLATION_READ_PARAM_CODE = "default_sync_only"


class InstallationReadConfig:
    """Resolve the migration mode from ``ac_common_config`` for one environment.

    A missing, disabled, unreadable, or malformed record deliberately keeps the
    complete repair path enabled. The value is read on each effective-capability
    access so an operator can roll an accepted environment back through DB
    configuration without waiting for a process restart or release.

    ``default_sync_only`` constructor argument exists only for direct unit-test wiring. The
    production DI binding always supplies ``common_config_service`` and ``env``.
    """

    def __init__(
        self,
        default_sync_only: bool = False,
        *,
        common_config_service: CommonConfigServiceProtocol | None = None,
        env: str | None = None,
    ) -> None:
        self._static_default_sync_only = default_sync_only
        self._common_config_service = common_config_service
        self._env = env

    @property
    def default_sync_only(self) -> bool:
        """Return the current environment's DB-owned migration switch."""
        if self._common_config_service is None or self._env is None:
            return self._static_default_sync_only
        try:
            value: Any = self._common_config_service.get_value(
                business_code=INSTALLATION_READ_BUSINESS_CODE,
                param_code=INSTALLATION_READ_PARAM_CODE,
                env=self._env,
                default=False,
                only_enabled=True,
            )
        except Exception:
            logger.exception(
                "[installation_read_config] common config read failed env=%s; "
                "retaining complete installation repair",
                self._env,
            )
            return False
        if type(value) is bool:
            return value
        if value is not False:
            logger.warning(
                "[installation_read_config] invalid common config value env=%s "
                "business_code=%s param_code=%s value=%r; retaining complete repair",
                self._env,
                INSTALLATION_READ_BUSINESS_CODE,
                INSTALLATION_READ_PARAM_CODE,
                value,
            )
        return False
