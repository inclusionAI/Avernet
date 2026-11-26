"""Core rate-limiting — provider-agnostic admission business logic.

``AdmissionController`` is the seam the HTTP adapter calls; it resolves the
applicable :class:`~gateway.community.spi.rate_limiter.RateLimitPolicy`, invokes
the injected :class:`~gateway.community.spi.rate_limiter.ApplicationRateLimiter`,
and applies the fail-open policy. No web framework and no storage backend appear
here (architecture Rule 7).
"""

from ._controller import (
    APPLICATION_SCOPE,
    AdmissionController,
    AdmissionOutcome,
    AdmissionResult,
)
from ._metrics import NullRateLimitMetrics, RateLimitMetrics
from ._policy import PolicyResolver, override_key

__all__ = [
    "APPLICATION_SCOPE",
    "AdmissionController",
    "AdmissionOutcome",
    "AdmissionResult",
    "NullRateLimitMetrics",
    "PolicyResolver",
    "RateLimitMetrics",
    "override_key",
]
