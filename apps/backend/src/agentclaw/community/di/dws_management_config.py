"""DWS command signing reuses the existing, boot-resolved principal secret."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field

from agentclaw.community.utils.gateway_principal_config import (
    get_principal_verifier_config,
)


@dataclass(frozen=True)
class DwsManagementConfig:
    key: bytes = field(repr=False)

    def bot_key(self, bot_id: str, owner_id: str) -> bytes:
        # COSEC: domain-separate command signatures from principal JWTs.
        # Neither the root secret nor derived keys leave Backend.
        context = json.dumps(
            ["dws-management-v1", bot_id, owner_id],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hmac.new(self.key, context.encode(), hashlib.sha256).hexdigest().encode()


def get_dws_management_config() -> DwsManagementConfig:
    return DwsManagementConfig(get_principal_verifier_config().signing_key.encode())
