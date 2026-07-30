from __future__ import annotations

from gateway.community.spi.authn import (
    CredentialBundle,
    Principal,
    PrincipalType,
)


class NoopAgentPassStrategy:
    name: str = "agentpass"
    principal_type: PrincipalType = PrincipalType.BOT

    async def build(self, creds: CredentialBundle) -> Principal | None:
        return None
