"""``access_key_token`` strategy — resolve an access-key token via the registry.

The caller presents a token in a dedicated header (``x-avernet-access-key-token``); the
strategy resolves it in **one** registry lookup:
``find_access_key_by_token(token) → RegisteredAccessKey | None``. There is no
separate validator abstraction — the lookup lives here.

Behaviour:

- no token presented → ``None`` (not applicable; runner fail-closes for
  ``access_key``);
- a token whose access key is unknown → ``None`` (soft miss, like the bot
  registry's ``find_bot_by_token → None``);
- a resolved access key → :class:`AccessKeyPrincipal` carrying the access key's
  ``access_key_id`` (the lookup token is NOT carried downstream — the access key
  is identified by id alone, by design).
"""

from __future__ import annotations

from gateway.community.spi.access_key import AccessKeyRegistry
from gateway.community.spi.authn import (
    AccessKey,
    AccessKeyPrincipal,
    CredentialBundle,
    Principal,
    PrincipalType,
)


class AccessKeyTokenStrategy:
    """Resolve a presented access-key token into an :class:`AccessKeyPrincipal`."""

    name = "access_key_token"
    principal_type = PrincipalType.ACCESS_KEY

    def __init__(
        self,
        registry: AccessKeyRegistry,
        token_header: str = "x-avernet-access-key-token",
    ) -> None:
        self._registry = registry
        self._token_header = token_header

    async def build(self, creds: CredentialBundle) -> Principal | None:
        token = creds.headers.get(self._token_header, "").strip()
        if not token:
            return None  # no access-key token → not applicable
        record = await self._registry.find_access_key_by_token(token)
        if record is None:
            return None  # unknown token → soft miss
        return AccessKeyPrincipal(
            tenant=record.tenant,
            access_key=AccessKey(
                access_key_id=record.access_key_id,
                access_key_token=token,
                expire_at=record.expire_at,
            ),
        )
