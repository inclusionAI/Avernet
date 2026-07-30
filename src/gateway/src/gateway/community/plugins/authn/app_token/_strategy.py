"""App-identity strategy — ``Authorization: Bearer <app_token>`` → ``AppPrincipal``.

Third-party tenant comes from the ``X-Tenant-Token`` header (authoritative) and
is cross-checked against the app-token record's tenant (design §6.3).

A Bearer that the validator does not recognize is treated as absent (returns
``None``), so other Bearer-based chains (e.g. bot_token) may resolve the same
credential (US27 — each chain adjudicates independently). Only a
recognized-but-tenant-mismatched token raises :class:`AuthError`.
"""

from __future__ import annotations

from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import (
    AppPrincipal,
    AppTokenValidator,
    CredentialBundle,
    Principal,
    PrincipalType,
    TenantResolver,
    ThirdPartyApp,
)

_TENANT_HEADER = "x-tenant-token"


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


class AppTokenStrategy:
    """Resolve a Bearer app token + tenant token into an :class:`AppPrincipal`."""

    name = "app_token"
    principal_type = PrincipalType.APP

    def __init__(self, keys: AppTokenValidator, tenants: TenantResolver) -> None:
        self._keys = keys
        self._tenants = tenants

    async def build(self, creds: CredentialBundle) -> Principal | None:
        app_token = _bearer(creds.headers.get("authorization"))
        if not app_token:
            return None  # no app token → strategy not applicable
        record = await self._keys.verify(app_token)
        if record is None:
            return None  # not one of mine → absent; another chain may resolve this Bearer (US27)
        tenant = await self._tenants.resolve(creds.headers.get(_TENANT_HEADER, ""))
        if record.tenant != tenant:
            raise AuthError("app token does not belong to the presented tenant")
        return AppPrincipal(
            tenant=tenant,
            app=ThirdPartyApp(
                app_id=record.app_id,
                app_name=record.app_name,
                owners=record.owners,
                app_type=record.app_type,
            ),
            on_behalf_of_opaque=creds.headers.get("x-end-user-id"),
        )
