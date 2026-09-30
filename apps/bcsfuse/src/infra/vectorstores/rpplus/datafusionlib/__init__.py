"""
RP+ (datafusionlib) Stub for Open-Core BCSFuse

This module is a STUB. The real RP+ datafusionlib has moved to
bcsfuse_internal.vectorstores.rpplus.datafusionlib.

RP+ is an internal-only service for user profiling and recommendations.
Open-core must use public vectorstore providers instead.

NOTE: This module does NOT import layotto or any internal dependencies.
"""

from .ServiceExecutor import (
    RPPlusInternalOnlyServiceUnavailable,
    ServiceExecutor,
    ServiceRequest,
    ServiceResponse,
    ServiceResponse_FacadeResponse,
)

__all__ = [
    "RPPlusInternalOnlyServiceUnavailable",
    "ServiceExecutor",
    "ServiceRequest",
    "ServiceResponse",
    "ServiceResponse_FacadeResponse",
]