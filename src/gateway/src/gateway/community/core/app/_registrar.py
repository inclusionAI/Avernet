"""AppRegistrar — register an app: mint a JWT, delegate persistence, return the record.

The issued JWT is stored as the ``token`` PK of ``avernet_apps`` via
:class:`AppRepository.store` (all DB touch lives in the repository); the authn
``app_token`` strategy resolves it by opaque DB lookup (``find_app_by_token``).
App tokens do NOT expire (the table has no ``expire_at``). Shares the gateway
HMAC key via :class:`PrincipalSigner.sign_token`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from gateway.community.spi.principal_signer import PrincipalSigner

from ._repository import AppRepository

_ISSUER = "gateway"


@dataclass(frozen=True)
class IssuedApp:
    """An app just registered: its record fields plus the freshly minted token."""

    app_id: str
    app_name: str
    owners: str
    app_type: str
    tenant: str
    token: str


class AppRegistrar:
    """Mint a JWT, persist it via the repository, return the record + token."""

    def __init__(
        self,
        repository: AppRepository,
        signer: PrincipalSigner,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._signer = signer
        self._clock = clock

    async def register(
        self,
        app_id: str,
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
    ) -> IssuedApp:
        claims = {
            "iss": _ISSUER,
            "typ": "app",
            "sub": app_id,
            "tenant": tenant,
            "iat": int(self._clock()),
            "jti": uuid.uuid4().hex,
        }
        token = await self._signer.sign_token(claims)
        await self._repository.store(
            token=token,
            app_id=app_id,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
        )
        return IssuedApp(
            app_id=app_id,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            token=token,
        )
