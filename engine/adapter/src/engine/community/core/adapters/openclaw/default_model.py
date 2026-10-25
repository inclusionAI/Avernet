"""Extract the configured default from a validated source snapshot."""
from __future__ import annotations

from typing import Any


def configured_default_model(snapshot: dict[str, Any]) -> str | None:
    """Preserve missing configuration, but refuse failed/malformed snapshots."""
    if snapshot.get("exists") is False:
        return None
    if snapshot.get("exists") is not True or snapshot.get("valid") is not True:
        raise ValueError("invalid model configuration snapshot")
    current = snapshot.get("parsed")
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
