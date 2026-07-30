"""App-identity strategy — ``Authorization: Bearer`` (or ``x-avernet-app-token``) → ``AppPrincipal``.

An ``Authorization: Bearer <app_token>`` is the default source; if absent, the
dedicated ``x-avernet-app-token`` header is used as a fallback (so a caller may
present the app token either way). The strategy resolves the app — and its
tenant — in one registry lookup: ``find_app_by_token(token) → RegisteredApp | None``.
The app record's ``tenant`` is the authoritative tenant for the principal.

A token that the registry does not recognise is treated as absent (returns
``None``), so other Bearer-based chains (e.g. bot_token) may resolve the same
credential (US27 — each chain adjudicates independently).
"""

from __future__ import annotations

from gateway.community.spi.app import AppRegistry
from gateway.community.spi.authn import (
    AppPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
    ThirdPartyApp,
)

_AUTH_HEADER = "authorization"


def extract_app_token(creds: CredentialBundle, dedicated_header: str) -> str | None:
    """Extract an app token: ``Authorization: Bearer`` first, else the dedicated header.

    Returns ``None`` when no usable app token is present.
    """
    auth: str = creds.headers.get(_AUTH_HEADER, "")
    if auth.lower().startswith("bearer"):
        token = auth[len("bearer") :].strip()
        if token:
            return token
    dedicated: str = creds.headers.get(dedicated_header, "").strip()
    if dedicated:
        return dedicated
    return None


class AppTokenStrategy:
    """Resolve a presented app token into an :class:`AppPrincipal`."""

    name = "app_token"
    principal_type = PrincipalType.APP

    def __init__(
        self, registry: AppRegistry, token_header: str = "x-avernet-app-token"
    ) -> None:
        self._registry = registry
        self._token_header = token_header

    async def build(self, creds: CredentialBundle) -> Principal | None:
        app_token = extract_app_token(creds, self._token_header)
        if not app_token:
            return None  # no app token → strategy not applicable
        record = await self._registry.find_app_by_token(app_token)
        if record is None:
            return None  # not one of mine → absent; another chain may resolve this Bearer (US27)
        return AppPrincipal(
            tenant=record.tenant,
            app=ThirdPartyApp(
                app_id=record.id,
                app_name=record.app_name,
                owners=record.owners,
                tenant=record.tenant,
                app_type=record.app_type,
            ),
        )
