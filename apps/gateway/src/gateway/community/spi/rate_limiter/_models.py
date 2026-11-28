"""Value types for the ApplicationRateLimiter SPI.

These are the only shapes that cross the semantic seam between the gateway's
business logic and a rate-limit storage provider. They name *what* the limiter
decides — a trusted application identity, a sustained rate and burst capacity,
and an allow/reject decision — and deliberately say nothing about *how* a
provider stores or computes it. No Redis key, Lua script, client, connection
string, HTTP object or caller-supplied header appears here or may appear in any
implementation's public surface (see the module docstring of ``_protocols``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitSubject:
    """The trusted identity a rate-limit budget is scoped to.

    Every field is derived from an authenticated principal and from deployment
    configuration — never from a caller-supplied header, query parameter or
    body. The tuple is the bucket's identity: two requests with the same subject
    draw from the same budget, and two subjects that differ in any field are
    isolated from each other.

    - ``namespace``: the deployment namespace, so two gateways that share a
      storage backend but serve different environments never collide.
    - ``tenant``: the tenant the authenticated application belongs to.
    - ``app_id``: the application's surrogate id (``avernet_application.id``).
      It is the authenticated app's own id; it is never ``0`` and never an
      empty/placeholder value — an unresolved application has no subject at all
      rather than a shared one (the admission layer returns no subject in that
      case, and the request keeps its pre-existing, unlimited behaviour).
    - ``scope``: the rule scope the policy was selected under (e.g. ``default``
      or a named override), so a future per-route or per-method rule isolates
      cleanly from the application-wide one without reusing another rule's
      bucket.
    """

    namespace: str
    tenant: str
    app_id: int
    scope: str

    def __post_init__(self) -> None:
        # Defence in depth: the admission layer already refuses to build a
        # subject for a missing/unresolved application, but the invariant that a
        # subject is never the shared empty/zero bucket is part of the contract,
        # so it is enforced at the type boundary too.
        if not self.namespace:
            raise ValueError("RateLimitSubject.namespace must be non-empty")
        if not self.tenant:
            raise ValueError("RateLimitSubject.tenant must be non-empty")
        if not self.scope:
            raise ValueError("RateLimitSubject.scope must be non-empty")
        if self.app_id <= 0:
            raise ValueError(
                "RateLimitSubject.app_id must be a positive application id; "
                "a missing or unresolved application must not be given a bucket"
            )


@dataclass(frozen=True)
class RateLimitPolicy:
    """A token-bucket budget: sustained ``rate`` with a ``burst`` ceiling.

    - ``rate``: sustained admissions per second once any burst is spent.
    - ``burst``: bucket capacity — the most requests admissible instantaneously
      from a full bucket. A new bucket starts full (``burst`` tokens).

    ``rate`` must be > 0 and ``burst`` must be >= 1: a policy that can admit
    nothing is a configuration error, never a runtime state.
    """

    rate: float
    burst: int

    def __post_init__(self) -> None:
        if not self.rate > 0:
            raise ValueError(f"RateLimitPolicy.rate must be > 0, got {self.rate!r}")
        if self.burst < 1:
            raise ValueError(
                f"RateLimitPolicy.burst must be >= 1 request, got {self.burst!r}"
            )


@dataclass(frozen=True)
class Decision:
    """The outcome of a single admission attempt.

    A decision is only ever *allowed* or *confirmed rejected*. A storage failure
    or timeout is NOT a decision — it is a dependency failure, surfaced as a
    :class:`~gateway.community.spi.rate_limiter.RateLimiterUnavailableError` exception
    so callers can tell "the budget is exhausted" apart from "the limiter could
    not answer" and apply a fail-open policy to the latter.

    - ``allowed``: whether this request consumed a token and may proceed.
    - ``retry_after_seconds``: for a rejection, the time until enough tokens have
      refilled for one admission; ``0.0`` for an allowed decision.
    - ``remaining_tokens``: tokens left in the bucket after this attempt, for
      observability. Fractional because the bucket refills continuously.
    """

    allowed: bool
    retry_after_seconds: float
    remaining_tokens: float

    @classmethod
    def allow(cls, *, remaining_tokens: float) -> Decision:
        return cls(
            allowed=True, retry_after_seconds=0.0, remaining_tokens=remaining_tokens
        )

    @classmethod
    def reject(cls, *, retry_after_seconds: float, remaining_tokens: float) -> Decision:
        return cls(
            allowed=False,
            retry_after_seconds=max(0.0, retry_after_seconds),
            remaining_tokens=remaining_tokens,
        )
