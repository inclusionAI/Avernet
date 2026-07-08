"""
PaaS health check public API types.

Re-exports all types consumers need. The private modules (_enums, _models, _protocols)
contain the actual definitions; this __init__.py is the public face of the package.
"""

from ._enums import PaaSProviderType
from ._models import HealthCheckerStrategyResult, PaasHealthCheckerResult
from ._protocols import PaaSHealthProvider

__all__ = [
    "PaaSProviderType",
    "HealthCheckerStrategyResult",
    "PaasHealthCheckerResult",
    "PaaSHealthProvider",
]
