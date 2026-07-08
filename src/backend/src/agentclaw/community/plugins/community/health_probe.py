"""CommunityHealthProbe — direct-HTTP engine health probe for the community profile.

Community ships no agentclawproxy / BaaS gateway, so this probes each ACTIVE
binding's engine ``/readiness`` endpoint **directly** over HTTP (the URL carried
in the binding's ``device_props``), never via arca proxypass. With no bindings
(the common community case) the list probes return ``[]``; sandbox-level health
is reported unsupported (no sandbox runtime). Health probing is an
"errors-are-the-result" surface — per-binding failures become ``unhealthy``
entries, never exceptions.

Real impl (not a ``MockSeam``); imports only ``core`` / ``plugin_api`` + httpx —
never ``plugins.prod`` / ``plugins.local`` — per the community isolation guard.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from injector import inject

from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.health_probe import HealthProbePlugin
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommunityHealthProbe(HealthProbePlugin):
    """Direct-HTTP readiness probes for the community profile."""

    @inject
    def __init__(self, binding_repo: DeviceBindingRepository) -> None:
        self._binding_repo = binding_repo

    @property
    def mode_label(self) -> str:
        return "community"

    def _list_active_bindings(self, staff_id: str) -> list:
        try:
            _, bindings = self._binding_repo.list_bindings(
                entity_id=staff_id,
                entity_type="staff",
                env=get_current_env(),
                status="ACTIVE",
                page=1,
                page_size=100,
            )
        except Exception as e:
            logger.error(
                "[CommunityHealthProbe] list_bindings failed staff_id=%s: %s",
                staff_id, e,
            )
            return []
        return list(bindings)

    async def _probe(self, binding) -> dict[str, Any]:
        props = binding.device_props or {}
        bot_id = props.get("bolt_id", "unknown")
        base = props.get("url") or props.get("http_url")
        if not base:
            return {
                "bot_id": bot_id,
                "device_id": binding.device_id,
                "state": "unknown",
                "reason": "no engine url in device_props",
                "engine": "openclaw",
                "checked_at": _now_iso(),
            }
        url = base.rstrip("/") + "/readiness"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(url)
            healthy = response.status_code == 200
            payload: dict[str, Any] = {}
            if response.content:
                try:
                    raw = response.json()
                    payload = raw if isinstance(raw, dict) else {}
                except Exception:
                    payload = {}
            return {
                "bot_id": bot_id,
                "device_id": binding.device_id,
                "state": payload.get("state") or ("ready" if healthy else "unhealthy"),
                "message": "ok" if healthy else "container probe failed",
                "engine": "openclaw",
                "version": payload.get("version"),
                "checked_at": _now_iso(),
            }
        except Exception as e:
            logger.warning(
                "[CommunityHealthProbe] container probe binding=%s: %s",
                binding.id, e,
            )
            return {
                "bot_id": bot_id,
                "device_id": binding.device_id,
                "state": "unhealthy",
                "reason": f"container probe failed: {e}",
                "engine": "openclaw",
                "checked_at": _now_iso(),
            }

    async def engine_health(self, staff_id: str) -> list[dict[str, Any]]:
        bindings = self._list_active_bindings(staff_id)
        if not bindings:
            return []
        return list(await asyncio.gather(*(self._probe(b) for b in bindings)))

    async def bots_health(self, staff_id: str) -> list[dict[str, Any]]:
        bindings = self._list_active_bindings(staff_id)
        if not bindings:
            return []
        raw = await asyncio.gather(*(self._probe(b) for b in bindings))
        return [
            {
                "bot_id": r.get("bot_id"),
                "device_id": r.get("device_id"),
                "healthy": r.get("state") == "ready",
                "engine_type": r.get("engine", "openclaw"),
                "error": r.get("reason"),
            }
            for r in raw
        ]

    async def sandbox_health(self, bot_id: str, owner_id: str) -> dict[str, Any]:
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "code": 1,
            "message": "community mode — no sandbox runtime",
            "checked_at": _now_iso(),
            "instances": [],
        }

    async def readiness(
        self, staff_id: str, grace_seconds: int,
    ) -> list[dict[str, Any]]:
        bindings = self._list_active_bindings(staff_id)
        if not bindings:
            return []
        return list(await asyncio.gather(*(self._probe(b) for b in bindings)))
