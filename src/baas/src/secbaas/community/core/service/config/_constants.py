"""System configuration key constants.

Centralized definition of system configuration keys used throughout
the application to avoid magic strings and ensure consistency.

Usage:
    from secbaas.community.core.service.config._constants import SystemConfigKey

    config = SystemConfigService.get_config(SystemConfigKey.ARCA_DFT_TENANT)
"""

from enum import StrEnum


class SystemConfigKey(StrEnum):
    """System configuration key constants.

    These keys are stored in the system_config table and represent
    important system-wide configuration values.

    Naming convention:
        {module}.{purpose} - e.g., "arca.dft_tenant"
    """

    # Arca PaaS platform configuration
    ARCA_DFT_TENANT = "arca.dft_tenant"
    """Default tenant ID for Arca PaaS platform (per environment).

    Value: template_id (int) as string
    Usage: Loaded via ArcaPaasService to get default Arca credentials
    """

    # Callback timeout configuration
    CALLBACK_TIMEOUT_SECONDS = "publish.callback_timeout_seconds"
    """System-level callback timeout in seconds.

    Value: integer string (e.g., "900")
    Usage: Override for bot callback timeout at system level,
    applied when no user-specified value exists.
    Falls back to DEFAULT_CALLBACK_TIMEOUT_SECONDS (900).
    """

    # Add more system config keys here as needed
    # Example:
    # ARCA_DEFAULT_TIMEOUT = "arca.default_timeout"
    # ARCA_BASE_URL = "arca.base_url"
