"""Compose the admission controller from configuration.

Separates two kinds of failure, exactly as the feature requires:

- **Configuration errors fail startup.** Rate limiting enabled with no provider
  (``plugins.rate_limiter = noop``), or an invalid budget, is a
  :class:`ConfigError` here (or a validation error at config load) — the gateway
  does not boot into a silently-unlimited or process-local state.
- **Runtime storage unavailability fails open**, and is handled later by the
  :class:`AdmissionController` / provider, not here. Building the controller
  never contacts the backend, so a Redis outage does not block startup.
"""

from __future__ import annotations

from gateway.community.config import UserConfig
from gateway.community.core.rate_limiting import AdmissionController, PolicyResolver
from gateway.community.spi.rate_limiter import ApplicationRateLimiter, RateLimitPolicy

from ._configs import ConfigError


def build_admission(
    *,
    user_config: UserConfig,
    limiter: ApplicationRateLimiter | None,
) -> AdmissionController | None:
    """Return a controller when rate limiting is enabled, else ``None``.

    ``limiter`` is the resolved ``plugins.rate_limiter`` provider — ``None`` when
    the selector is ``noop``. A ``None`` limiter while ``enabled`` is a
    configuration error (fail startup), never a silent fallback.
    """
    rl = user_config.rate_limit
    if not rl.enabled:
        return None

    if limiter is None:
        raise ConfigError(
            "rate_limit.enabled is true but plugins.rate_limiter selects no "
            "provider ('noop'). Select a provider (e.g. 'redis') or an enterprise "
            "option; the gateway will not fall back to unlimited or process-local "
            "limiting."
        )

    # The config validator guarantees ``default`` is present when enabled; assert
    # it so a future change cannot quietly produce a controller with no default.
    if rl.default is None:  # pragma: no cover - guarded by config validation
        raise ConfigError("rate_limit.enabled is true but rate_limit.default is unset")

    default = RateLimitPolicy(rate=rl.default.rate, burst=rl.default.burst)
    overrides = {
        key: RateLimitPolicy(rate=policy.rate, burst=policy.burst)
        for key, policy in rl.overrides.items()
    }
    resolver = PolicyResolver(default=default, overrides=overrides)
    return AdmissionController(
        limiter=limiter,
        policy_resolver=resolver,
        namespace=rl.namespace,
    )
