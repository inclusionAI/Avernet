"""Fail-closed materialization transports used until external contracts bind."""
from __future__ import annotations

from pathlib import Path

from engine.community.core.resource_materialization.models import (
    MaterializationRequest,
    MaterializationResult,
)


class NotConfiguredBaasMaterializationClient:
    async def pull(
        self,
        request: MaterializationRequest,
        destination: Path,
    ) -> None:
        raise RuntimeError("baas_materialization_not_configured")


class NotConfiguredBackendMaterializationCallbackClient:
    async def report(self, result: MaterializationResult) -> None:
        raise RuntimeError("backend_materialization_callback_not_configured")
