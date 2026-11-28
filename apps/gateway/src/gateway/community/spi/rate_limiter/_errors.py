"""Typed failures for the ApplicationRateLimiter SPI.

The admission contract draws a hard line between two things an HTTP adapter must
never confuse:

- a *confirmed rejection* — the budget is exhausted — which is a
  :class:`~gateway.community.spi.rate_limiter.Decision` with ``allowed=False``;
- a *dependency failure* — the limiter could not reach a verdict because its
  storage is unavailable, timed out, or answered unusably — which is one of the
  exceptions below.

A dependency failure must surface as an exception rather than a rejection so the
caller can fail *open* (forward the request) instead of reporting a false quota
exhaustion. An ambiguous timeout is the sharp case: the token may or may not
have been consumed, so the operation is never retried (that would risk double
consumption); the caller treats it as a dependency failure and forwards.
"""

from __future__ import annotations


class RateLimiterError(Exception):
    """Base class for ApplicationRateLimiter failures."""


class RateLimiterUnavailableError(RateLimiterError):
    """The limiter could not produce a decision due to a storage dependency.

    Raised for a connection failure, a bounded-timeout expiry, or a malformed
    response from the backend. It explicitly does NOT mean the budget is
    exhausted — a caller that maps this to HTTP 429 or 503 would report a
    storage outage as quota exhaustion, which the contract forbids. The caller
    fails open (forwards the request) and records the failure separately from
    confirmed rejections.

    ``ambiguous`` marks the case where the admission operation may have been
    applied on the backend (a timeout after the command was sent): the token may
    already be consumed, so the operation must not be automatically retried.
    """

    def __init__(self, message: str, *, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous
