"""
DefaultConfigService Protocol — read-only access to an engine's default
configuration file.

Engines that ship a default-config blob (OpenClaw reads
``/home/admin/agentclaw-daas-scripts/confs/openclaw/openclaw.json``)
implement this Protocol; engines without one return ``None`` for
``Engine.default_config`` and the route 501s.

Errors raised by the implementation:

* :class:`FileNotFoundError` → 404
* :class:`IsADirectoryError` → 400 (path resolved to a directory)
* :class:`ValueError` → 500 (JSON parse failure)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.community.core.default_config.models import DefaultConfigResult
from engine.community.core.engine.context import AuthContext


@runtime_checkable
class DefaultConfigService(Protocol):
    """Backend reads engine default-config through this Protocol."""

    async def get_default_config(
        self, auth: AuthContext | None = None,
    ) -> DefaultConfigResult:
        """Return the resolved path + parsed JSON of the engine's default
        configuration."""
        ...


__all__ = ["DefaultConfigService"]
