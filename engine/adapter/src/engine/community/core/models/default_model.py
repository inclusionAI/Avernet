"""Optional default-model read contract, independent of catalogue support.

A configured default is an engine-owned model identifier, or None when that
engine has no default configured. It need not occur in the catalogue. This read
does not resolve session/request overrides and must not modify configuration.
Upstream failures must raise rather than look like an unconfigured default.
Existing ModelsService providers need not implement this optional extension.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.engine.context import AuthContext


@runtime_checkable
class DefaultModelService(Protocol):
    async def get_default_model(self, auth: AuthContext | None = None) -> str | None:
        """Return the engine's default, without session/request overrides."""
        ...
