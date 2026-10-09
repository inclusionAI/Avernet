"""
Environment Variable Secret Provider

OSS-friendly secret provider that retrieves secrets from environment variables.
Suitable for open-source deployments without internal secret management (MIST).
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class EnvSecretProvider:
    """
    Environment variable-based secret provider for OSS deployments.

    This provider retrieves secrets from environment variables.
    It's suitable for open-source deployments that don't have access
    to internal secret management systems like MIST.

    For internal deployments, use the internal secret provider that
    retrieves secrets from MIST or other internal secret management systems.

    Security Note:
    - This provider does NOT log secret values
    - Secrets are retrieved only from OS environment variables
    - Consider using a proper secret management system for production
    """

    def __init__(self, prefix: str = ""):
        """Initialize secret provider.

        Args:
            prefix: Optional prefix for environment variable names.
                   If provided, secrets will be looked up as {prefix}{key}.
        """
        self.prefix = prefix

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get secret value by key.

        Args:
            key: Secret key (will be prefixed with self.prefix if set)
            default: Default value if secret not found

        Returns:
            Secret value from environment variable or default.

        Note:
            This method does NOT log the secret value.
        """
        env_key = f"{self.prefix}{key}" if self.prefix else key
        value = os.getenv(env_key, default)

        # Log that we retrieved a secret (but not its value)
        if value is not None:
            logger.debug(f"EnvSecretProvider: retrieved secret '{env_key}'")
        else:
            logger.debug(f"EnvSecretProvider: secret '{env_key}' not found, using default")

        return value

    def require_secret(self, key: str) -> str:
        """Get secret value by key, raising error if not found.

        Args:
            key: Secret key (will be prefixed with self.prefix if set)

        Returns:
            Secret value from environment variable.

        Raises:
            KeyError: If secret not found.

        Note:
            This method does NOT log the secret value.
        """
        env_key = f"{self.prefix}{key}" if self.prefix else key
        value = os.getenv(env_key)

        if value is None:
            raise KeyError(f"Secret '{env_key}' not found in environment variables")

        # Log that we retrieved a secret (but not its value)
        logger.debug(f"EnvSecretProvider: retrieved required secret '{env_key}'")

        return value