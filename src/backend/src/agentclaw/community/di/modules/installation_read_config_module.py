"""Installation Reader binding for the DB-owned completed-backfill gate."""

from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.api.common_config_service import CommonConfigServiceProtocol
from agentclaw.community.core.skill_center.installation_read_config import InstallationReadConfig
from agentclaw.community.utils.env_utils import get_current_env


class InstallationReadConfigModule(Module):
    """Bind a runtime DB-configured Installation Reader mode."""

    @singleton
    @provider
    def installation_read(
        self, common_config_service: CommonConfigServiceProtocol
    ) -> InstallationReadConfig:
        """Use the normalized deployment environment's ``ac_common_config`` row."""
        return InstallationReadConfig(
            common_config_service=common_config_service,
            env=get_current_env(),
        )
