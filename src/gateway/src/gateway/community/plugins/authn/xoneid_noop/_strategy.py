from __future__ import annotations

from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
)


class NoopXOneIdStrategy:
    name: str = "xoneid"
    principal_type: PrincipalType = PrincipalType.USER

    async def build(self, creds: CredentialBundle) -> Principal | None:
        return None
