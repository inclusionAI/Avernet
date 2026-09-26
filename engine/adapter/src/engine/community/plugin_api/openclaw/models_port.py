"""OpenClawModelsPort — native port for model-catalogue operations.

Models is token-agnostic (the gateway `models.list` / `providers.list` RPCs
take no per-user routing token), so port methods take NO token parameter and the
impl uses `_default_client()`.  Returns raw payload lists; the
`core/adapters/openclaw/models.py` adapter builds `Model` / `Provider` DTOs +
handles the `models.list → providers.list` fallback.

NOTE: this module is named `models_port.py` (not `models.py`) to avoid
shadowing the pre-existing `plugin_api/openclaw/models.py` shared-types module.
The Port class is exported as `OpenClawModelsPort` in `__init__` and port.py.
"""
from __future__ import annotations

from typing import Any, Protocol


class OpenClawModelsPort(Protocol):
    """Native model-catalogue operations over the OpenClaw gateway."""

    async def models_list(self) -> list[dict[str, Any]]:
        """Raw model entries from `models.list` RPC (timeout 5s).

        Falls back to `providers.list` when `models.list` returns no entries or
        errors.  Returns `[]` when both fail (logged).  The fallback logic lives
        in the impl so the adapter only sees a flat list of model dicts.
        """
        ...

    async def providers_list(self) -> list[dict[str, Any]]:
        """Raw provider entries from `providers.list` RPC.

        Returns `[]` on failure (logged).  Each entry is a provider dict that
        may nest a `models` list; the adapter flattens/builds DTOs.
        """
        ...


__all__ = ["OpenClawModelsPort"]
