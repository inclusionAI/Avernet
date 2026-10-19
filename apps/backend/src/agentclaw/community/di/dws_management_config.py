"""Optional deployment credential; never accepted from an HTTP caller."""

import os
import hashlib
import hmac
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DwsManagementConfig:
    key: bytes = field(repr=False)

    def bot_key(self, bot_id: str, owner_id: str) -> bytes:
        # COSEC: only the derived per-owner/bot key may be provisioned to a
        # container. Never distribute the backend master key to Bot workloads.
        context = json.dumps(
            ["dws-management-v1", bot_id, owner_id],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hmac.new(self.key, context.encode(), hashlib.sha256).hexdigest().encode()


def get_dws_management_config() -> DwsManagementConfig:
    # Provision independently per environment via the deployment secret store.
    return DwsManagementConfig(os.environ.get("DWS_MANAGEMENT_MASTER_KEY", "").encode())
