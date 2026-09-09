"""Current-runtime MCP capability readiness probe."""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTimeoutError,
    DeviceAdapterTransport,
)

MCP_READINESS_TIMEOUT_SECONDS = 8.0
logger = get_logger()


class McpRuntimeReadinessStatus(StrEnum):
    READY = "READY"
    NOT_CAPABLE = "NOT_CAPABLE"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class McpRuntimeReadinessResult:
    status: McpRuntimeReadinessStatus
    engine: str
    reason: str | None
    retryable: bool

    @classmethod
    def ready(cls, *, engine: str) -> "McpRuntimeReadinessResult":
        return cls(
            status=McpRuntimeReadinessStatus.READY,
            engine=engine,
            reason=None,
            retryable=False,
        )


class _ReadinessData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: McpRuntimeReadinessStatus
    engine: str
    reason: str | None
    retryable: bool


class _ReadinessEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    success: Literal[True]
    data: _ReadinessData


class CurrentMcpRuntimeProbeService:
    """Ask the exact fenced binding's Engine whether MCP delivery is ready."""

    def __init__(
        self,
        *,
        resolver: DeviceContextResolver,
        adapter_transport: DeviceAdapterTransport,
    ) -> None:
        self._resolver = resolver
        self._transport = adapter_transport

    async def probe_binding(
        self,
        *,
        binding_id: int,
        bot_id: str,
        owner_id: str,
        engine: str,
    ) -> McpRuntimeReadinessResult:
        try:
            context = self._resolver.resolve_for_binding_invoke(
                binding_id,
                owner_id,
                bot_id=bot_id,
            )
            response = await self._transport.invoke(
                context.conn_info,
                "GET",
                "/api/mcp/readiness",
                timeout=MCP_READINESS_TIMEOUT_SECONDS,
            )
        except DeviceAdapterEndpointNotFoundError:
            return self._result(
                McpRuntimeReadinessStatus.NOT_CAPABLE,
                engine,
                "mcp_readiness_endpoint_absent",
            )
        except DeviceNotBoundError:
            return self._result(
                McpRuntimeReadinessStatus.NOT_CAPABLE,
                engine,
                "current_runtime_not_bound",
            )
        except UnknownProviderError:
            return self._result(
                McpRuntimeReadinessStatus.INVALID,
                engine,
                "current_runtime_provider_invalid",
            )
        except DeviceAdapterHTTPStatusError as error:
            if error.status_code >= 500 or error.status_code in {408, 425, 429}:
                return self._result(
                    McpRuntimeReadinessStatus.TRANSIENT_ERROR,
                    engine,
                    f"adapter_http_{error.status_code}",
                )
            return self._result(
                McpRuntimeReadinessStatus.INVALID,
                engine,
                f"adapter_http_{error.status_code}",
            )
        except DeviceAdapterTimeoutError:
            return self._result(
                McpRuntimeReadinessStatus.TRANSIENT_ERROR,
                engine,
                "adapter_request_timeout",
            )
        except Exception as error:
            logger.warning(
                "[mcp_runtime_probe] adapter request failed: binding_id=%s "
                "bot_id=%s engine=%s error_type=%s traceback=%s",
                binding_id,
                bot_id,
                engine,
                type(error).__name__,
                "".join(traceback.format_tb(error.__traceback__)),
            )
            return self._result(
                McpRuntimeReadinessStatus.TRANSIENT_ERROR,
                engine,
                "adapter_request_failed",
            )

        try:
            envelope = _ReadinessEnvelope.model_validate(response)
        except ValidationError:
            return self._result(
                McpRuntimeReadinessStatus.INVALID,
                engine,
                "invalid_readiness_response",
            )
        data = envelope.data
        if data.engine != engine or data.retryable != (
            data.status is McpRuntimeReadinessStatus.TRANSIENT_ERROR
        ):
            return self._result(
                McpRuntimeReadinessStatus.INVALID,
                engine,
                "inconsistent_readiness_response",
            )
        return McpRuntimeReadinessResult(
            status=data.status,
            engine=data.engine,
            reason=data.reason,
            retryable=data.retryable,
        )

    @staticmethod
    def _result(
        status: McpRuntimeReadinessStatus,
        engine: str,
        reason: str,
    ) -> McpRuntimeReadinessResult:
        return McpRuntimeReadinessResult(
            status=status,
            engine=engine,
            reason=reason,
            retryable=status is McpRuntimeReadinessStatus.TRANSIENT_ERROR,
        )


__all__ = [
    "CurrentMcpRuntimeProbeService",
    "MCP_READINESS_TIMEOUT_SECONDS",
    "McpRuntimeReadinessResult",
    "McpRuntimeReadinessStatus",
]
