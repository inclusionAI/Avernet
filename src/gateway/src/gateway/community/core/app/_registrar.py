"""AppRegistrar — register an app: mint a JWT, delegate persistence, return the record.

The issued JWT is stored as the ``token`` of ``avernet_application`` via
:class:`AppRepository.store` (all DB touch lives in the repository); the authn
``app_token`` strategy resolves it by opaque DB lookup (``find_app_by_token``).
App tokens do NOT expire (the table has no ``expire_at``). Shares the gateway
HMAC key via :class:`PrincipalSigner.sign_token`. The app's stable identity is
its surrogate ``id`` (returned by ``store``); the JWT ``sub`` is the human-readable
``app_name`` (the surrogate ``id`` is not known until after insert).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from gateway.community.spi.principal_signer import PrincipalSigner

from ._repository import AppRepository

_ISSUER = "gateway"


@dataclass(frozen=True)
class IssuedApp:
    """An app just registered: its record fields plus the freshly minted token."""

    id: int
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
        app_name: str,
        owners: str,
        app_type: str,
        tenant: str,
        *,
        status: str = "ACTIVE",
        env: str = "",
        config: dict[str, Any] | None = None,
    ) -> IssuedApp:
        claims = {
            "iss": _ISSUER,
            "typ": "app",
            "sub": app_name,
            "tenant": tenant,
            "iat": int(self._clock()),
            "jti": uuid.uuid4().hex,
        }
        token = await self._signer.sign_token(claims)
        app_id = await self._repository.store(
            token=token,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            status=status,
            env=env,
            config=config,
        )
        return IssuedApp(
            id=app_id,
            app_name=app_name,
            owners=owners,
            app_type=app_type,
            tenant=tenant,
            token=token,
        )
