"""Public Configuration Providers for open-source bcsfuse.

This package provides configuration providers that use environment variables
and YAML configuration files for open-source deployments.

Available Providers:
- YamlEnvConfigProvider: YAML + environment variable configuration
- DrmConfigProvider: DRM-like configuration using environment variables
"""

from __future__ import annotations

__all__ = [
    "YamlEnvConfigProvider",
    "DrmConfigProvider",
]


def __getattr__(name: str):
    """Lazy import providers to avoid import-time dependencies."""
    if name == "YamlEnvConfigProvider":
        from src.infra.public.config.yaml_env_config_provider import (
            YamlEnvConfigProvider
        )
        return YamlEnvConfigProvider

    if name == "DrmConfigProvider":
        from src.infra.public.config.drm_config_provider import (
            DrmConfigProvider
        )
        return DrmConfigProvider

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")