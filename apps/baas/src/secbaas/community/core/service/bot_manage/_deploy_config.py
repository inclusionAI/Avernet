"""Deployment overrides and callback configuration shared by bot operations."""

from secbaas.community.api.device_manage import DeployConfig
from secbaas.community.api.publish_manage import DEFAULT_CALLBACK_TIMEOUT_SECONDS
from secbaas.community.core.repository.system_config import SystemConfigRepository
from secbaas.community.core.service.config import SystemConfigKey
from secbaas.community.core.utils.env_utils import get_current_env
from secbaas.community.logger import get_logger

logger = get_logger("core-service")


def resolve_callback_timeout(
    user_value: int | None,
    system_config_repo: SystemConfigRepository | None = None,
) -> int:
    """Resolve callback_timeout_seconds with 3-tier priority.

    Priority chain (highest to lowest):
    1. User bot config value
    2. System config value
    3. Code constant (DEFAULT_CALLBACK_TIMEOUT_SECONDS)

    Args:
        user_value: User-specified value from bot config (may be None)
        system_config_repo: Optional system config repository for tier 2 lookup

    Returns:
        Resolved timeout in seconds
    """
    if user_value is not None:
        return user_value
    if system_config_repo is not None:
        try:
            env = get_current_env()
            record = system_config_repo.get_by_env_and_key(
                env, SystemConfigKey.CALLBACK_TIMEOUT_SECONDS
            )
            if record and record.conf_value:
                return int(record.conf_value)
        except Exception:
            logger.warning(
                "Failed to read callback_timeout from system config", exc_info=True
            )
    logger.info(
        "System config repo is not available, use the DEFAULT_CALLBACK_TIMEOUT_SECONDS"
    )
    return DEFAULT_CALLBACK_TIMEOUT_SECONDS


def merge_deploy_config(
    current: DeployConfig | None,
    override: DeployConfig | dict | None,
) -> DeployConfig | None:
    """Merge override fields into current deploy_config at field level.

    Preserve unrelated fields when applying a sparse override. Unlike the
    other fields, ``docker_image`` is request-scoped: omission or None clears
    the previous override so template/provider defaults can apply.
    """
    if current is None and override is None:
        return None
    current_data = current.model_dump(exclude_none=True) if current else {}
    override_data = (
        override.model_dump(exclude_none=True)
        if isinstance(override, DeployConfig)
        else override or {}
    )
    merged = {**current_data, **override_data}
    merged["docker_image"] = override_data.get("docker_image")
    return DeployConfig.model_validate(merged)
