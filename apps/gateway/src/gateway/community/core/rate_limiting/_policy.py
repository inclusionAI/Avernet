"""Policy resolution — which token-bucket budget applies to an application.

A deployment configures one default budget and, optionally, per-application
overrides keyed by ``tenant:app_id``. Resolution is pure and synchronous: given
a tenant and app id it returns the applicable :class:`RateLimitPolicy`, with an
override taking precedence over the default.

The resolver holds only validated policies — construction fails loudly on a
non-positive rate or a sub-one burst (see :class:`RateLimitPolicy`) so a broken
budget is a startup error, never a silent "unlimited" at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from gateway.community.spi.rate_limiter import RateLimitPolicy


def override_key(tenant: str, app_id: int) -> str:
    """The lookup key for a per-application override: ``tenant:app_id``."""
    return f"{tenant}:{app_id}"


@dataclass(frozen=True)
class PolicyResolver:
    """Resolves the applicable :class:`RateLimitPolicy` for an application.

    ``default`` applies to every application without a more specific entry;
    ``overrides`` maps ``tenant:app_id`` to a replacement budget.
    """

    default: RateLimitPolicy
    overrides: dict[str, RateLimitPolicy]

    def resolve(self, *, tenant: str, app_id: int) -> RateLimitPolicy:
        return self.overrides.get(override_key(tenant, app_id), self.default)
