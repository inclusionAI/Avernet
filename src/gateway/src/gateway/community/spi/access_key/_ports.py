"""Access-key-domain SPI — the ``AccessKeyRegistry`` contract.

An access key is resolved from a presented token by an :class:`AccessKeyRegistry`
implementation (the canonical ORM impl lives in ``core/access_key``). The authn
``access_key_token`` strategy depends on this interface, not on the impl.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class RegisteredAccessKey:
    """An access key a registry resolves a token to (registry record).

    ``tenant`` is the tenant the access key belongs to; ``expire_at`` is when
    the access key expires.
    """

    access_key_id: str
    tenant: str
    expire_at: datetime


class AccessKeyRegistry(Protocol):
    """Read-only access-key store keyed by token (resolved by the
    ``access_key_token`` strategy).

    ``find_access_key_by_token`` returns ``None`` for an unknown token (soft
    miss — not applicable), never raising on a bad token.
    """

    async def find_access_key_by_token(
        self, token: str
    ) -> RegisteredAccessKey | None: ...
