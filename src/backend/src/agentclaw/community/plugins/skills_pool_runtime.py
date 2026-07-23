"""通过当前 Bot binding 调用容器内 Skills Pool 激活端点。"""

from __future__ import annotations

from typing import Any

from injector import inject

from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    CurrentRuntimeLayoutProbeService,
    RuntimeLayoutProbeResult,
)
from agentclaw.community.core.skills_pool.models import (
    PoolCutoverResult,
    PoolCutoverStatus,
    PoolSkillMapping,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)

logger = get_logger()


class SkillsPoolRuntime:
    """ARCA/BaaS 共用的 adapter transport 实现。"""

    @inject
    def __init__(
        self,
        *,
        resolver: DeviceContextResolver,
        adapter_transport: DeviceAdapterTransport,
        probe_service: CurrentRuntimeLayoutProbeService,
    ) -> None:
        self._resolver = resolver
        self._transport = adapter_transport
        self._probe = probe_service

    async def probe(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
    ) -> RuntimeLayoutProbeResult:
        return await self._probe.probe_bot(
            bot_id=bot_id,
            user_id=user_id,
            engine=engine,
        )

    async def cutover(
        self,
        *,
        bot_id: str,
        user_id: str,
        migration_generation: str,
        preparation_id: str,
        registered_local_names: list[str],
        mappings: list[PoolSkillMapping],
    ) -> PoolCutoverResult:
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/activate",
                body={
                    "migration_generation": migration_generation,
                    "preparation_id": preparation_id,
                    "registered_local_names": registered_local_names,
                    "mappings": [mapping.to_dict() for mapping in mappings],
                },
            )
        except Exception as error:
            logger.exception(
                "[skills_pool.runtime] cutover failed bot_id=%s generation=%s",
                bot_id,
                migration_generation,
            )
            return PoolCutoverResult(
                committed=False,
                status=PoolCutoverStatus.TRANSIENT_ERROR,
                evidence={
                    "reason": "runtime_cutover_request_failed",
                    "error_type": type(error).__name__,
                },
            )
        data = response.get("data")
        if not isinstance(data, dict):
            return PoolCutoverResult(
                committed=False,
                status=PoolCutoverStatus.INVALID,
                evidence={"reason": "runtime_cutover_response_invalid"},
            )
        raw_status = str(data.get("status", ""))
        try:
            status = PoolCutoverStatus(raw_status)
        except ValueError:
            status = PoolCutoverStatus.UNKNOWN
        evidence = dict(data.get("evidence") or {})
        if status is PoolCutoverStatus.UNKNOWN:
            evidence["raw_status"] = raw_status
        committed = data.get("committed") is True and status in {
            PoolCutoverStatus.COMMITTED,
            PoolCutoverStatus.ALREADY_COMMITTED,
        }
        return PoolCutoverResult(
            committed=committed,
            status=status,
            evidence=evidence,
        )

    async def publish_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
    ) -> bool:
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/mappings/publish",
                body={"mappings": [mapping.to_dict() for mapping in mappings]},
            )
        except Exception:
            logger.exception(
                "[skills_pool.runtime] mapping publish failed bot_id=%s",
                bot_id,
            )
            return False
        return response.get("success") is True

    async def verify_mappings(
        self,
        *,
        bot_id: str,
        user_id: str,
        mappings: list[PoolSkillMapping],
    ) -> bool:
        try:
            response = await self._invoke(
                bot_id=bot_id,
                user_id=user_id,
                path="/api/skills/layout/mappings/verify",
                body={"mappings": [mapping.to_dict() for mapping in mappings]},
            )
        except Exception:
            logger.exception(
                "[skills_pool.runtime] mapping verify failed bot_id=%s",
                bot_id,
            )
            return False
        data = response.get("data")
        return (
            response.get("success") is True
            and isinstance(data, dict)
            and data.get("valid") is True
        )

    async def _invoke(
        self,
        *,
        bot_id: str,
        user_id: str,
        path: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._resolver.resolve_for_bot(bot_id, user_id)
        return await self._transport.invoke(
            context.conn_info,
            "POST",
            path,
            body=body,
            timeout=30.0,
        )


# Compatibility for callers introduced by the initial OpenClaw rollout.
OpenClawSkillsPoolRuntime = SkillsPoolRuntime


__all__ = ["OpenClawSkillsPoolRuntime", "SkillsPoolRuntime"]
