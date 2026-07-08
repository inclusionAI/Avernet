"""HealthProbePlugin — per-runtime probe surface for /api/system/health.

Each endpoint in ``api/system/router.py`` has the same shape:
"in local boots, do X in-process; in prod, talk to agentclawproxy". This
plugin moves that branching out of the route. Each impl knows its own
target.

Current impls:
- ``plugins.local.health_probe.LocalHealthProbe``
- ``plugins.prod.health_probe.ProdHealthProbe``
"""
from __future__ import annotations

from typing import Any, Protocol
from agentclaw.community.plugin_api.base import Plugin


class HealthProbePlugin(Plugin, Protocol):
    """Per-runtime probes for the system-health endpoints."""

    @property
    def mode_label(self) -> str:
        """Label included in response payloads' ``mode`` field. Local impl
        returns ``"local"``; prod impl returns the env name."""
        ...

    async def engine_health(self, staff_id: str) -> list[dict[str, Any]]:
        """Probe every bot's OpenClaw engine. Result shape:
        ``[{"state": "ready"|"unhealthy", ...}, ...]``."""
        ...

    async def bots_health(self, staff_id: str) -> list[dict[str, Any]]:
        """Probe every bot's sandbox/process pair. Result shape:
        ``[{"bot_id", "healthy", ...}, ...]``."""
        ...

    async def sandbox_health(
        self, bot_id: str, owner_id: str,
    ) -> dict[str, Any]:
        """Probe one bot's sandbox. Returns ``{"code", "message",
        "instances", ...}``. Local impl returns
        ``code=1, message="Local mode not supported"``."""
        ...

    async def readiness(
        self, staff_id: str, grace_seconds: int,
    ) -> list[dict[str, Any]]:
        """Per-bot runtime readiness for the frontend."""
        ...
