"""LocalHealthProbe — BaaS-backed probes for local/singlebox boots (plan-04).

Replaces the legacy LocalProcessManager-based probe with one that walks
ACTIVE bindings from DeviceBindingRepository and probes each container's
``/readiness`` endpoint via ``BaasService.get_http_info``.

Health probing is a "errors are the result" surface — per-binding failures
do not propagate; they produce ``unhealthy`` / ``error`` entries in the
result list so a single offline device doesn't 500 the whole list endpoint.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from injector import inject

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.service_bot.services.baas_service import BaasService
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.health_probe import HealthProbePlugin
from agentclaw.community.plugin_api.impl_registry import Flavor, Mode, plugin_impl
from agentclaw.community.plugins.local._mock_seam import MockSeam
from agentclaw.community.utils.env_utils import get_current_env


logger = get_logger()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


@plugin_impl(
    mode=Mode.LOCAL,
    flavor=Flavor.FAKE,
    rationale="BaaS get_http_info → httpx /readiness probe per binding",
)
class LocalHealthProbe(MockSeam, HealthProbePlugin):
    @inject
    def __init__(
        self,
        binding_repo: DeviceBindingRepository,
        baas_service: BaasService,
        bot_repository: BotRepository,
    ) -> None:
        self._binding_repo = binding_repo
        self._baas_service = baas_service
        self._bot_repository = bot_repository

    @property
    def mode_label(self) -> str:
        return "local"

    async def engine_health(self, staff_id: str) -> list[dict[str, Any]]:
        """Walk ACTIVE local bindings for staff_id; probe each container's
        ``/readiness`` via BaaS http_info. Per-binding failure → unhealthy
        entry, not exception (spec §4.6)."""
        bindings = self._list_active_local_bindings(staff_id)
        if not bindings:
            return []
        return list(await asyncio.gather(*(
            self._probe_binding_readiness(b, is_engine=True)
            for b in bindings
        )))

    def _list_active_local_bindings(self, staff_id: str):
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
                "[LocalHealthProbe] list_bindings failed staff_id=%s: %s",
                staff_id, e,
            )
            return []
        return [b for b in bindings if b.device_provider == "local"]

    async def _probe_binding_readiness(
        self, binding, *, is_engine: bool
    ) -> dict[str, Any]:
        """Resolve container URL via BaaS http_info, GET /readiness.

        `is_engine` toggles result shape between `engine_health`
        (uses {state, message, reason}) and `readiness`
        (uses {state, version, age_seconds, ...}). Both share the
        same probe path."""
        props = binding.device_props or {}
        bot_id = props.get("bolt_id", "unknown")
        device_id = binding.device_id
        adapter_port = props.get("adapter_port")

        if adapter_port is None:
            return {
                "bot_id": bot_id,
                "device_id": device_id,
                "state": "unknown",
                "reason": "no adapter_port in device_props",
                "engine": "openclaw",
                "checked_at": _now_iso(),
            }

        try:
            info = self._baas_service.get_http_info(
                bind_id=binding.id,
                port=adapter_port,
                path="/readiness",
                device_affinity=binding.entity_id,
                tenant=props.get("tenant") or None,
            )
        except Exception as e:
            logger.warning(
                "[LocalHealthProbe] get_http_info binding=%s: %s",
                binding.id, e,
            )
            return {
                "bot_id": bot_id,
                "device_id": device_id,
                "state": "unhealthy",
                "reason": f"BaaS get_http_info failed: {e}",
                "engine": "openclaw",
                "checked_at": _now_iso(),
            }

        # http_url 已含 /readiness（get_http_info path=/readiness 已拼），不再拼避免 double 404。
        url = info.http_url
        headers = {"openclawToken": info.token}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(url, headers=headers)
            healthy = response.status_code == 200
            payload: dict[str, Any] = {}
            if response.content:
                try:
                    payload_raw = response.json()
                    payload = payload_raw if isinstance(payload_raw, dict) else {}
                except Exception:
                    payload = {}
            state = (
                payload.get("state")
                or ("ready" if healthy else "unhealthy")
            )
            return {
                "bot_id": bot_id,
                "device_id": device_id,
                "state": state,
                "message": "ok" if healthy else "container probe failed",
                "engine": "openclaw",
                "version": payload.get("version"),
                "checked_at": _now_iso(),
            }
        except Exception as e:
            logger.warning(
                "[LocalHealthProbe] container probe binding=%s: %s",
                binding.id, e,
            )
            return {
                "bot_id": bot_id,
                "device_id": device_id,
                "state": "unhealthy",
                "reason": f"container probe failed: {e}",
                "engine": "openclaw",
                "checked_at": _now_iso(),
            }

    async def bots_health(self, staff_id: str) -> list[dict[str, Any]]:
        """Aggregate health for staff_id's bots — same probe path as
        engine_health, formatted with `healthy` bool + latency for the
        frontend dashboard."""
        bindings = self._list_active_local_bindings(staff_id)
        if not bindings:
            return []
        raw = await asyncio.gather(*(
            self._probe_binding_readiness(b, is_engine=False)
            for b in bindings
        ))
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

    async def sandbox_health(
        self, bot_id: str, owner_id: str,
    ) -> dict[str, Any]:
        # singlebox doesn't expose sandbox-level health (no Arca sandbox);
        # keep parity with legacy behavior — return "not supported" so the
        # /api/system/sandbox-health endpoint stays callable.
        return {
            "bot_id": bot_id,
            "owner_id": owner_id,
            "code": 1,
            "message": "Local mode not supported",
            "checked_at": _now_iso(),
            "instances": [],
        }

    async def readiness(
        self, staff_id: str, grace_seconds: int,
    ) -> list[dict[str, Any]]:
        """Same probe path as engine_health; grace_seconds is reserved
        for future "in-startup" tolerance — current impl returns container
        state as-is (spec §4.6: errors are the result)."""
        bindings = self._list_active_local_bindings(staff_id)
        if not bindings:
            return []
        return list(await asyncio.gather(*(
            self._probe_binding_readiness(b, is_engine=False)
            for b in bindings
        )))
