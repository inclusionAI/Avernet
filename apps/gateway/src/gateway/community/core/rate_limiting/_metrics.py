"""Metrics seam for admission outcomes.

The admission controller reports each outcome to a :class:`RateLimitMetrics`
sink. The community default is a no-op; a deployment (or the enterprise overlay)
can supply a real sink to emit counters. The point of the seam is that a
*dependency failure* is reported through a different method than a *confirmed
rejection*, so an operator can alert on "the limiter is failing open" without it
being drowned in normal 429s.
"""

from __future__ import annotations

from typing import Protocol

from gateway.community.spi.rate_limiter import RateLimitSubject


class RateLimitMetrics(Protocol):
    """Sink for admission-outcome counters."""

    def on_allowed(self, subject: RateLimitSubject) -> None:
        """A request was admitted (a token was consumed)."""
        ...

    def on_rejected(
        self, subject: RateLimitSubject, *, retry_after_seconds: float
    ) -> None:
        """A request was refused — the budget is confirmed exhausted."""
        ...

    def on_dependency_failure(
        self, subject: RateLimitSubject, *, ambiguous: bool
    ) -> None:
        """The limiter could not decide; the request was forwarded (fail open).

        This is counted separately from :meth:`on_rejected` precisely so a
        storage outage is never read as quota exhaustion.
        """
        ...


class NullRateLimitMetrics(RateLimitMetrics):
    """No-op metrics sink — the community default."""

    def on_allowed(self, subject: RateLimitSubject) -> None:  # noqa: D102
        return None

    def on_rejected(
        self, subject: RateLimitSubject, *, retry_after_seconds: float
    ) -> None:  # noqa: D102
        return None

    def on_dependency_failure(
        self, subject: RateLimitSubject, *, ambiguous: bool
    ) -> None:  # noqa: D102
        return None
