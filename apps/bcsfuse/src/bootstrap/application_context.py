"""
BCSFuse Application Context

Provides centralized access to configuration and providers.
"""
import os
from typing import Optional

from src.bootstrap.provider_registry import ProviderRegistry


class ApplicationContext:
    """
    Explicit dependency context for a BCSFuse deployment.

    Holds configuration and provider registry for the application.
    Supports runtime, dev, and test modes.
    """

    def __init__(
        self,
        *,
        mode: str,
        startup_profile: str,
        registry: ProviderRegistry,
    ) -> None:
        """Initialize application context.

        Args:
            mode: Provider mode (runtime, dev, test).
            startup_profile: Deployment composition name.
            registry: Fully assembled provider registry.
        """
        self.mode = mode
        self.startup_profile = startup_profile
        self._registry = registry

    @property
    def registry(self) -> ProviderRegistry:
        """Get provider registry."""
        return self._registry

    @property
    def config(self):
        """Get configuration provider."""
        return self.registry.get("config")

    def get_provider(self, name: str):
        """Get a provider by name.

        Args:
            name: Provider name.

        Returns:
            Provider instance.
        """
        return self.registry.get(name)


def build_application_context(mode: Optional[str] = None) -> ApplicationContext:
    """Build application context.

    Args:
        mode: Provider mode (runtime, dev, dev_smoke, test).
              If None, reads from BCSFUSE_PROVIDER_MODE env var.
              Defaults to 'dev' if not set.

    Returns:
        Configured ApplicationContext instance.
    """
    if mode is None:
        mode = os.getenv("BCSFUSE_PROVIDER_MODE", "dev")

    # Validate mode
    valid_modes = ["runtime", "dev", "dev_smoke", "test"]
    if mode not in valid_modes:
        raise ValueError(
            f"Invalid provider mode: {mode}. Must be one of: {valid_modes}"
        )

    from src.bootstrap.opensource import build_opensource_provider_registry

    registry = build_opensource_provider_registry(mode=mode)
    return ApplicationContext(
        mode=mode,
        startup_profile="opensource",
        registry=registry,
    )
