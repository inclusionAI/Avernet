"""App-domain SPI — the ``AppRegistry`` contract.

A third-party app is resolved from a presented token by an :class:`AppRegistry`
implementation (the canonical ORM impl lives in ``core/app``). The authn
``app_token`` strategy depends on this interface, not on the impl.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegisteredApp:
    """A third-party app a registry resolves a token to (registry record).

    ``id`` is the app's surrogate bigint id (``avernet_application.id``) — its
    stable identity. ``tenant`` is the tenant the app belongs to — the
    authoritative tenant for the resulting
    :class:`~gateway.community.spi.authn.AppPrincipal` (read from the app-token
    record, no longer cross-checked against a tenant header).
    """

    id: int
    app_name: str
    owners: str
    app_type: str
    tenant: str


class AppRegistry(Protocol):
    """Read-only third-party-app store keyed by token (resolved by the
    ``app_token`` strategy).

    ``find_app_by_token`` returns ``None`` for an unknown token (soft miss —
    not applicable), never raising on a bad token.
    """

    async def find_app_by_token(self, token: str) -> RegisteredApp | None: ...
