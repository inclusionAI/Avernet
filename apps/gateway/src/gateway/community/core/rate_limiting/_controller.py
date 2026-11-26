"""Admission control — the provider-agnostic business logic.

:class:`AdmissionController` turns an authenticated identity set into one of four
outcomes, invoking the semantic :class:`ApplicationRateLimiter` and applying the
fail-open policy. It knows nothing about HTTP or about any storage backend; the
HTTP adapter maps its :class:`AdmissionOutcome` to a status code, and the limiter
is injected. This is the single place that decides *who* is limited and *how a
storage failure is handled*, so swapping the Redis provider for an enterprise one
changes nothing here.

Eligibility (this MVP): only a request carrying an authenticated
:class:`AppPrincipal` is limited, keyed by the app's ``tenant`` + ``app_id``.
Different tokens of the same application resolve to the same subject and so share
one budget. Every other identity shape — a user, a bot, an access key, or no
principal at all — is NOT applicable: it is forwarded exactly as before, never
grouped into a shared empty/zero bucket.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from gateway.community.logger import get_logger
from gateway.community.spi.authn import AppPrincipal, Principal, PrincipalType
from gateway.community.spi.rate_limiter import (
    ApplicationRateLimiter,
    RateLimiterUnavailableError,
    RateLimitSubject,
)

from ._metrics import NullRateLimitMetrics, RateLimitMetrics
from ._policy import PolicyResolver

logger = get_logger("rate_limiter")

# The rule scope for the single application-wide budget in this MVP. It is a
# constant key segment, isolating this rule's buckets from any future per-route
# or per-method rule without those rules sharing state.
APPLICATION_SCOPE = "app"


class AdmissionResult(Enum):
    """The four distinguishable admission outcomes."""

    #: No eligible application identity — forward with pre-existing behaviour.
    NOT_APPLICABLE = auto()
    #: A token was consumed — forward.
    ALLOWED = auto()
    #: Budget confirmed exhausted — the adapter returns 429 and does not forward.
    REJECTED = auto()
    #: The limiter could not decide (storage failure/timeout) — fail open, forward.
    FAILED_OPEN = auto()


@dataclass(frozen=True)
class AdmissionOutcome:
    """Result of an admission decision, for the HTTP adapter to map."""

    result: AdmissionResult
    retry_after_seconds: float = 0.0

    @property
    def should_reject(self) -> bool:
        """True only for a confirmed rejection — the one case that blocks the call."""
        return self.result is AdmissionResult.REJECTED


_NOT_APPLICABLE = AdmissionOutcome(AdmissionResult.NOT_APPLICABLE)


class AdmissionController:
    """Decides admission for a request from its resolved identity set.

    Args:
        limiter: the semantic rate limiter (community Redis or enterprise).
        policy_resolver: resolves the applicable budget per application.
        namespace: deployment namespace mixed into the bucket subject.
        metrics: outcome sink (defaults to no-op).
    """

    def __init__(
        self,
        *,
        limiter: ApplicationRateLimiter,
        policy_resolver: PolicyResolver,
        namespace: str,
        metrics: RateLimitMetrics | None = None,
    ) -> None:
        self._limiter = limiter
        self._policies = policy_resolver
        self._namespace = namespace
        self._metrics = metrics or NullRateLimitMetrics()

    def _subject_for(
        self, identities: dict[PrincipalType, Principal]
    ) -> RateLimitSubject | None:
        """The bucket subject for an eligible application request, else ``None``.

        Only an :class:`AppPrincipal` is eligible. A missing or non-positive
        ``app_id`` yields ``None`` (never a shared ``app_id=0`` bucket): such a
        request keeps its existing unlimited behaviour.
        """
        principal = identities.get(PrincipalType.APP)
        if not isinstance(principal, AppPrincipal):
            return None
        tenant = principal.tenant
        app_id = principal.app.app_id
        if not tenant or app_id <= 0:
            # An AppPrincipal is expected to carry both; if it somehow does not,
            # do not invent a shared bucket — forward as before and say so.
            logger.warning(
                "app principal missing tenant/app_id for rate limiting "
                "(tenant=%r app_id=%r); forwarding without limiting",
                tenant,
                app_id,
            )
            return None
        return RateLimitSubject(
            namespace=self._namespace,
            tenant=tenant,
            app_id=app_id,
            scope=APPLICATION_SCOPE,
        )

    async def admit(
        self, identities: dict[PrincipalType, Principal]
    ) -> AdmissionOutcome:
        subject = self._subject_for(identities)
        if subject is None:
            return _NOT_APPLICABLE

        policy = self._policies.resolve(tenant=subject.tenant, app_id=subject.app_id)
        try:
            decision = await self._limiter.try_acquire(subject, policy)
        except RateLimiterUnavailableError as exc:
            # Fail OPEN: a storage failure must never be reported as quota
            # exhaustion. Record it distinctly from a rejection and forward.
            self._metrics.on_dependency_failure(subject, ambiguous=exc.ambiguous)
            logger.warning(
                "rate-limit dependency failure (ambiguous=%s) for tenant=%s app_id=%s; "
                "failing open: %s",
                exc.ambiguous,
                subject.tenant,
                subject.app_id,
                exc,
            )
            return AdmissionOutcome(AdmissionResult.FAILED_OPEN)

        if decision.allowed:
            self._metrics.on_allowed(subject)
            return AdmissionOutcome(AdmissionResult.ALLOWED)

        self._metrics.on_rejected(
            subject, retry_after_seconds=decision.retry_after_seconds
        )
        logger.info(
            "rate limit exceeded for tenant=%s app_id=%s retry_after=%.3fs",
            subject.tenant,
            subject.app_id,
            decision.retry_after_seconds,
        )
        return AdmissionOutcome(
            AdmissionResult.REJECTED,
            retry_after_seconds=decision.retry_after_seconds,
        )
