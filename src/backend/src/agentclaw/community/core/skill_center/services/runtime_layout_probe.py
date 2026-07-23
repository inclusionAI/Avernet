"""Current-runtime orchestration for the Skills Pool layout probe."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from injector import inject
from pydantic import BaseModel, ConfigDict, ValidationError

from agentclaw.community.core.devices.services.device_context_resolver import (
    DeviceContextResolver,
)
from agentclaw.community.core.devices.services.device_context import (
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterEndpointNotFoundError,
    DeviceAdapterHTTPStatusError,
    DeviceAdapterTransport,
)


LAYOUT_CONTRACT_VERSION = "skills-pool-p3-v1"


class RuntimeLayoutProbeStatus(str, Enum):
    READY = "READY"
    NOT_CAPABLE = "NOT_CAPABLE"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    INVALID = "INVALID"


@dataclass(frozen=True)
class RuntimePoolLayout:
    pool_root: Path
    marker: Path

    @classmethod
    def for_home(cls, home: str | Path = "/home/admin") -> "RuntimePoolLayout":
        pool_root = Path(home) / ".openclaw" / "workspace" / "skills-pool"
        return cls(pool_root=pool_root, marker=pool_root / ".pool-ready")

    @classmethod
    def for_engine(
        cls,
        engine: str,
        home: str | Path = "/home/admin",
    ) -> "RuntimePoolLayout":
        if engine == "openclaw":
            return cls.for_home(home)
        if engine == "claude_code":
            pool_root = Path(home) / ".claude_code" / "workspace" / "skills-pool"
            return cls(pool_root=pool_root, marker=pool_root / ".pool-ready")
        raise ValueError(f"engine Pool layout not implemented: {engine}")


@dataclass(frozen=True)
class RuntimeLayoutProbeResult:
    status: RuntimeLayoutProbeStatus
    engine: str
    layout_contract_version: str
    preparation_id: str | None
    evidence: dict[str, Any]


class _RuntimeLayoutProbeData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: RuntimeLayoutProbeStatus
    engine: str
    layout_contract_version: str
    preparation_id: str | None
    evidence: dict[str, Any]


class _RuntimeLayoutProbeEnvelope(BaseModel):
    success: Literal[True]
    data: _RuntimeLayoutProbeData


class CurrentRuntimeLayoutProbeService:
    """Resolve the Bot's active binding and ask that adapter to inspect itself."""

    @inject
    def __init__(
        self,
        *,
        resolver: DeviceContextResolver,
        adapter_transport: DeviceAdapterTransport,
    ) -> None:
        self._resolver = resolver
        self._transport = adapter_transport

    async def probe_bot(
        self,
        *,
        bot_id: str,
        user_id: str,
        engine: str,
        layout: RuntimePoolLayout | None = None,
    ) -> RuntimeLayoutProbeResult:
        if engine == "teclaw":
            return self._not_capable(
                engine,
                "engine_has_no_filesystem_pool_layout",
            )
        if engine not in {"openclaw", "claude_code"}:
            return self._not_capable(engine, "engine_pool_probe_not_implemented")
        layout = layout or RuntimePoolLayout.for_engine(engine)

        try:
            context = self._resolver.resolve_for_bot(bot_id, user_id)
            response = await self._transport.invoke(
                context.conn_info,
                "POST",
                "/api/skills/layout/probe",
                body={
                    "engine": engine,
                    "layout_contract_version": LAYOUT_CONTRACT_VERSION,
                },
                timeout=10.0,
            )
        except DeviceNotBoundError:
            return self._not_capable(engine, "current_runtime_not_bound")
        except UnknownProviderError as error:
            return self._invalid_control_plane(
                engine,
                layout,
                "current_runtime_provider_invalid",
                error,
            )
        except DeviceAdapterEndpointNotFoundError:
            return await self._confirm_runtime_without_probe_endpoint(
                conn_info=context.conn_info,
                engine=engine,
                layout=layout,
            )
        except DeviceAdapterHTTPStatusError as error:
            return self._classify_http_status_error(
                engine=engine,
                layout=layout,
                error=error,
            )
        except Exception as error:
            return self._transient(engine, layout, error)

        return self._parse_response(response, engine=engine, layout=layout)

    async def _confirm_runtime_without_probe_endpoint(
        self,
        *,
        conn_info: dict[str, Any],
        engine: str,
        layout: RuntimePoolLayout,
    ) -> RuntimeLayoutProbeResult:
        """Treat 404 as old-image capability only after adapter liveness succeeds."""
        try:
            await self._transport.invoke(
                conn_info,
                "GET",
                "/health",
                timeout=5.0,
            )
        except DeviceAdapterHTTPStatusError as error:
            return self._classify_http_status_error(
                engine=engine,
                layout=layout,
                error=error,
            )
        except Exception as error:
            return self._transient(engine, layout, error)
        return self._not_capable(
            engine,
            "runtime_layout_probe_endpoint_absent",
        )

    @staticmethod
    def _classify_http_status_error(
        *,
        engine: str,
        layout: RuntimePoolLayout,
        error: DeviceAdapterHTTPStatusError,
    ) -> RuntimeLayoutProbeResult:
        if error.status_code >= 500 or error.status_code in {408, 425, 429}:
            return CurrentRuntimeLayoutProbeService._transient(
                engine,
                layout,
                error,
            )
        return CurrentRuntimeLayoutProbeService._invalid_control_plane(
            engine,
            layout,
            "runtime_probe_rejected",
            error,
        )

    @staticmethod
    def _parse_response(
        response: dict[str, Any],
        *,
        engine: str,
        layout: RuntimePoolLayout,
    ) -> RuntimeLayoutProbeResult:
        try:
            envelope = _RuntimeLayoutProbeEnvelope.model_validate(response)
        except ValidationError:
            return CurrentRuntimeLayoutProbeService._invalid_response(engine, layout)
        data = envelope.data
        if (
            data.engine != engine
            or data.layout_contract_version != LAYOUT_CONTRACT_VERSION
            or (
                data.status is RuntimeLayoutProbeStatus.READY
                and not isinstance(data.preparation_id, str)
            )
        ):
            return CurrentRuntimeLayoutProbeService._invalid_response(engine, layout)
        return RuntimeLayoutProbeResult(
            status=data.status,
            engine=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=data.preparation_id,
            evidence=data.evidence,
        )

    @staticmethod
    def _invalid_control_plane(
        engine: str,
        layout: RuntimePoolLayout,
        reason: str,
        error: Exception,
    ) -> RuntimeLayoutProbeResult:
        return RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.INVALID,
            engine=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=None,
            evidence={
                "reason": reason,
                "marker": str(layout.marker),
                "error_type": type(error).__name__,
            },
        )

    @staticmethod
    def _not_capable(engine: str, reason: str) -> RuntimeLayoutProbeResult:
        return RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
            engine=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=None,
            evidence={"reason": reason},
        )

    @staticmethod
    def _invalid_response(
        engine: str, layout: RuntimePoolLayout
    ) -> RuntimeLayoutProbeResult:
        return RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.INVALID,
            engine=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=None,
            evidence={
                "reason": "invalid_runtime_probe_response",
                "marker": str(layout.marker),
            },
        )

    @staticmethod
    def _transient(
        engine: str,
        layout: RuntimePoolLayout,
        error: Exception,
    ) -> RuntimeLayoutProbeResult:
        return RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.TRANSIENT_ERROR,
            engine=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=None,
            evidence={
                "reason": "runtime_probe_failed",
                "marker": str(layout.marker),
                "error_type": type(error).__name__,
                "error": str(error),
            },
        )


# Compatibility for callers introduced by the initial OpenClaw rollout.
OpenClawPoolLayout = RuntimePoolLayout


__all__ = [
    "CurrentRuntimeLayoutProbeService",
    "LAYOUT_CONTRACT_VERSION",
    "OpenClawPoolLayout",
    "RuntimePoolLayout",
    "RuntimeLayoutProbeResult",
    "RuntimeLayoutProbeStatus",
]
