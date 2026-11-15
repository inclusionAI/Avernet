"""
RP+ (datafusionlib) ServiceExecutor Stub for Open-Core BCSFuse

This module is a STUB. The real RP+ ServiceExecutor implementation
is not available in open-source.

RP+ is an internal-only service for user profiling and recommendations.
Open-core must use public vectorstore providers instead.

NOTE: This module does NOT import any internal dependencies.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class RPPlusInternalOnlyServiceUnavailable(RuntimeError):
    """Raised when attempting to use internal-only RP+ service in open-core."""

    def __init__(self, message: str = ""):
        super().__init__(
            message or
            "RP+ (datafusionlib) service is not available in open-core. "
            "Use public vectorstore providers such as:\n"
            "  - InMemoryVectorStore\n"
            "  - QdrantLocalVectorStore\n"
            "  - FaissSqliteVectorStore"
        )


class ServiceRequest:
    """Stub: RP+ service request (not available in open-core)."""

    def __init__(
        self,
        bizId: Optional[str] = None,
        serviceName: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, str]] = None
    ):
        raise RPPlusInternalOnlyServiceUnavailable()


class ServiceResponse:
    """Stub: RP+ service response (not available in open-core)."""

    def __init__(
        self,
        datas: List[Dict[str, Any]],
        data: Dict[str, Any],
        dataMode: str,
        serviceName: str,
        traceId: str,
        bizId: str,
        errorMsg: str,
        errorCode: str,
        success: bool
    ):
        raise RPPlusInternalOnlyServiceUnavailable()


class ServiceResponse_FacadeResponse:
    """Stub: RP+ service response wrapper (not available in open-core)."""

    def __init__(self, *args, **kwargs):
        raise RPPlusInternalOnlyServiceUnavailable()


class ServiceExecutor:
    """Stub: RP+ service executor (not available in open-core)."""

    def __init__(self, *args, **kwargs):
        raise RPPlusInternalOnlyServiceUnavailable()

    def execute(self, *args, **kwargs):
        """Stub: Execute RP+ service."""
        raise RPPlusInternalOnlyServiceUnavailable()


__all__ = [
    "RPPlusInternalOnlyServiceUnavailable",
    "ServiceRequest",
    "ServiceResponse",
    "ServiceResponse_FacadeResponse",
    "ServiceExecutor",
]
