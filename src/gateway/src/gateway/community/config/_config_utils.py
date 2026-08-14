"""Config utility functions — cached access and dot-path resolution."""

from __future__ import annotations

from typing import Any

from ._config_loader import ConfigLoader
from ._models import Config

_config: Config | None = None


def get_config(*, reload: bool = False) -> Config:
    """Return the singleton ``Config``, loading it on first call.

    Pass ``reload=True`` to force a fresh load from disk.
    """
    global _config  # noqa: PLW0603
    if _config is None or reload:
        _config = ConfigLoader.load()
    return _config


def reset_config() -> None:
    """Reset the cached config (useful in tests)."""
    global _config  # noqa: PLW0603
    _config = None


def get_config_by_path(
    config: Config,
    path: str,
    default: Any = None,
) -> Any:
    """Resolve a dot-separated path against *config* and return the value.

    Each segment is looked up as an attribute, then as a dict key.
    """
    if not path:
        return default

    segments = path.split(".")
    current: Any = config

    for segment in segments:
        try:
            current = getattr(current, segment)
        except AttributeError:
            try:
                current = current[segment]
            except (KeyError, TypeError):
                return default

    return current
