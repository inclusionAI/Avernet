"""Extract the configured default from a validated configuration response."""
from __future__ import annotations

from typing import Any


def configured_default_model(config: dict[str, Any]) -> str | None:
    """Preserve missing configuration, but refuse failed/malformed configuration responses."""
    if config.get("exists") is False:
        return None
    if config.get("exists") is not True or config.get("valid") is not True:
        raise ValueError("invalid model configuration response")
    current = config.get("parsed")
    for key in ("agents", "defaults", "model"):
        if not isinstance(current, dict):
            raise ValueError("invalid model configuration")
        if key not in current:
            return None
        current = current[key]
    if isinstance(current, dict):
        if "primary" not in current:
            return None
        current = current["primary"]
    if not isinstance(current, str) or not current.strip():
        raise ValueError("invalid configured default model")
    return current
