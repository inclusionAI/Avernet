"""InstallationReadConfig binding for the completed-backfill migration gate."""

from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.core.skill_center.installation_read_config import (
    InstallationReadConfig,
)
from agentclaw.community.di.modules import config_module
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


class InstallationReadConfigModule(Module):
    """Bind the environment-specific Installation Reader mode."""

    @singleton
    @provider
    def installation_read(self) -> InstallationReadConfig:
        """Choose Default-only sync only for an explicitly accepted environment."""
        block = config_module.read_user_config().get("skill_installation", {})
        if not isinstance(block, dict) or set(block) - {"default_sync_only"}:
            raise ValueError("skill_installation must contain only default_sync_only")
        modes = block.get("default_sync_only", {})
        if not isinstance(modes, dict) or set(modes) - {"pre", "prod"}:
            raise ValueError("skill_installation.default_sync_only must map pre/prod")
        if any(type(value) is not bool for value in modes.values()):
            raise ValueError("skill_installation.default_sync_only values must be booleans")
        env = get_current_env()
        config = InstallationReadConfig(default_sync_only=modes.get(env, False))
        logger.info(
            "[installation_read_config] env=%s default_sync_only=%s",
            env,
            config.default_sync_only,
        )
        return config
