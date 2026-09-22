"""Pinned PaaS command execution over the existing BaaS Service API.

Use for lifecycle operations requiring a particular physical container rather
than bot-level load balancing. Callers resolve IDs from an authorized bot first.
"""
from urllib.parse import quote

from agentclaw.community.core.service_bot.baas_service_errors import BaasServiceError


class DeviceCommandsMixin:
    def exec_command_on_device(self, *, paas_device_id: str, cmd: str) -> dict:
        if not isinstance(paas_device_id, str) or not paas_device_id:
            raise ValueError("paas_device_id is required")
        response = self._http.post(
            f"/api/v1/paas/devices/{quote(paas_device_id, safe='@')}/commands",
            json={"cmd": cmd}, timeout=40.0,
        )
        response.raise_for_status()
        envelope = response.json()
        if not isinstance(envelope, dict) or envelope.get("code") != 0:
            raise BaasServiceError("Pinned container command failed")
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise BaasServiceError("Invalid pinned container command result")
        return data
