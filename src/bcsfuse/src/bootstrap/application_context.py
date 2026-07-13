"""
Application Context for OSS Deployment

Provides centralized access to configuration and providers.
"""
import os
from typing import Optional


class ApplicationContext:
    """
    Application Context for OSS Deployment.

    Holds configuration and provider registry for the application.
    Supports runtime, dev, and test modes.
    """

    def __init__(self, mode: str = "dev"):
        """Initialize application context.

        Args:
            mode: Provider mode (runtime, dev, test).
        """
        self.mode = mode
        self._registry = None
        self._config = None

    @property
    def registry(self):
        """Get provider registry."""
        if self._registry is None:
            from src.bootstrap.opensource import build_opensource_provider_registry
            self._registry = build_opensource_provider_registry(mode=self.mode)
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

    return ApplicationContext(mode=mode)