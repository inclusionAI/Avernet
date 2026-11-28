"""ApplicationRateLimiter Protocol — the semantic admission contract.

This is the single seam between the gateway's business logic and a rate-limit
storage provider. Core logic depends only on this interface; an HTTP adapter
maps its outcomes to HTTP behaviour. An enterprise provider (e.g. TBase/ZCache)
is a drop-in replacement for the community Redis provider and changes no
business logic.

What the public surface must NOT expose (in types, arguments, return values or
raised exceptions): Redis keys or clients, Lua scripts or Redis commands,
connection strings or credentials, internal SDK types, or HTTP request/response
objects. Those are implementation details that live entirely inside a provider.

Admission atomicity — the core requirement a provider must satisfy — is stated
on :meth:`ApplicationRateLimiter.try_acquire`. It is deliberately impossible to
satisfy by composing a separate read and write (e.g. ``CachePlugin.get`` then
``set`` under a process-local lock): a process-local lock does not serialise two
gateway instances that share one backend, so concurrent admissions would both
read the same token count and both consume it. Refill, check, consume and
persist must be one atomic step on the shared backend.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._models import Decision, RateLimitPolicy, RateLimitSubject


@runtime_checkable
class ApplicationRateLimiter(Protocol):
    """Distributed, per-application token-bucket admission control."""

    async def try_acquire(
        self, subject: RateLimitSubject, policy: RateLimitPolicy
    ) -> Decision:
        """Attempt to admit one request against *subject*'s shared budget.

        The implementation MUST, as one atomic operation on the shared backend:

        1. refill the bucket for *subject* based on elapsed time and
           ``policy.rate``, capped at ``policy.burst`` (a never-seen bucket
           starts full, i.e. at ``policy.burst`` tokens);
        2. check whether at least one token is available;
        3. if so, consume exactly one token;
        4. persist the new bucket state (token count and update time).

        Each call accounts for exactly one request, whatever the response body
        turns out to be — a streaming/SSE response is one admission, not one per
        chunk. Callers never refund a token for an upstream failure.

        Returns an allowed :class:`Decision` when a token was consumed, or a
        rejected :class:`Decision` (carrying the time until a token refills) when
        the budget is exhausted.

        Raises
        ------
        RateLimiterUnavailableError
            When the backend is unreachable, a bounded timeout expires, or the
            backend returns an unusable response. This is a dependency failure,
            NOT a rejection: the caller fails open. On an ambiguous timeout the
            implementation MUST NOT automatically retry (the token may already
            have been consumed) and MUST raise with ``ambiguous=True``.
        """
        ...

    async def close(self) -> None:
        """Release backend resources (connection pools, clients).

        Must be idempotent and safe to call during shutdown.
        """
        ...
