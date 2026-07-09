"""HTTP-layer authenticated user model.

``AuthenticatedUser`` is the adapter-owned identity DTO that HTTP
routers depend on. It is converted at the auth-dependency boundary
from whatever plugin-specific user model the auth plugin returns
(``AuthenticatedIdentity`` in prod, the local cookie identity in dev). The
goal is to stop the plugin's user model from leaking through the
HTTP layer: routers depend on this adapter type, not on a Plugin API
class.

Field names match the neutral ``AuthenticatedIdentity`` shape so existing
router code (``user.staffId``, ``user.nickName``, etc.) keeps reading
correctly. The semantic change is the *type*, not the *attribute
surface*.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedUser:
    """User identity surfaced to HTTP routers — adapter-owned.

    Populated by ``get_current_user()`` from the auth plugin's own
    user model. Frozen so endpoints cannot mutate identity once
    resolved.
    """

    id: str
    """Provider-specific user ID (SSO id in corp, OIDC subject in
    community, a synthesized id in local mode)."""

    staffId: str  # noqa: N815 — attribute kept in camelCase to match the
    """Staff/work number — the canonical handle that core services key on."""
    # historical AuthenticatedIdentity surface so router bodies that already
    # write ``user.staffId`` keep working without a sweeping rename.

    operatorName: str  # noqa: N815
    """Account / login name."""

    nickName: str | None = None  # noqa: N815
    """Display name."""

    tenantId: str | None = None  # noqa: N815
    """Tenant id, if available."""
