"""ApplicationRateLimiter SPI — semantic, provider-agnostic admission control.

The gateway limits a request by asking an :class:`ApplicationRateLimiter`
whether one token is available for a trusted :class:`RateLimitSubject` under a
:class:`RateLimitPolicy`. The limiter answers with a :class:`Decision`
(allowed / confirmed-rejected) or raises
:class:`RateLimiterUnavailableError` when its storage dependency cannot answer.

The community Redis provider lives in
``gateway.community.plugins.rate_limiter.redis``; an enterprise provider is
registered through ``gateway.community.plugin_registry`` without the community
package importing it.
"""

from ._errors import RateLimiterError, RateLimiterUnavailableError
from ._models import Decision, RateLimitPolicy, RateLimitSubject
from ._protocols import ApplicationRateLimiter

__all__ = [
    "ApplicationRateLimiter",
    "Decision",
    "RateLimitPolicy",
    "RateLimitSubject",
    "RateLimiterError",
    "RateLimiterUnavailableError",
]
