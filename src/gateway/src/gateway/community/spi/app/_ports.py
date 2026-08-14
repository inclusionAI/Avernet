"""App-domain SPI — the ``AppRegistry`` contract.

A third-party app is resolved from a presented credential by an
:class:`AppRegistry` implementation (the canonical ORM impl lives in
``core/app``). The authn ``app_token`` strategy depends on this interface, not
on the impl — in particular it does not know how the credential is stored or
verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegisteredApp:
    """A third-party app a registry resolves a credential to (registry record).

    ``id`` is the app's surrogate bigint id (``avernet_application.id``) — its
    stable identity. ``tenant`` is the tenant the app belongs to — the
    authoritative tenant for the resulting
    :class:`~gateway.community.spi.authn.AppPrincipal` (read from the app
    record, no longer cross-checked against a tenant header).
    """

    id: int
    app_name: str
    owners: str
    app_type: str
    tenant: str


class AppRegistry(Protocol):
    """Third-party-app store resolving a presented credential to its app.

    ``find_app_by_credential`` returns ``None`` for anything it does not
    recognise — a malformed credential, an unknown one, one whose app is not
    ``ACTIVE``, or one that fails verification. All of these are soft misses
    (not applicable), never exceptions, so another identity chain may still
    claim the same credential.
    """

    async def find_app_by_credential(self, credential: str) -> RegisteredApp | None: ...
