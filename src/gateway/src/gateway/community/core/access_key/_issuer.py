"""AccessKeyIssuer — issue an access key: mint a JWT, delegate persistence, return the record.

The issued JWT is stored as the ``token`` PK of ``avernet_access_key_token`` via
:class:`AccessKeyRepository.store` (all DB touch lives in the repository); the
authn ``access_key_token`` strategy resolves it by opaque DB lookup
(``find_access_key_by_token``). The JWT's claims are for the caller / downstream
to self-verify; the gateway only matches the string. Shares the gateway HMAC
key via :class:`PrincipalSigner.sign_token`.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from gateway.community.spi.principal_signer import PrincipalSigner

from ._repository import AccessKeyRepository

_ISSUER = "gateway"


@dataclass(frozen=True)
class IssuedAccessKey:
    """An access key just issued: its record fields plus the freshly minted token."""

    access_key: str
    tenant: str
    expire_at: datetime
    token: str


class AccessKeyIssuer:
    """Mint a JWT, persist it via the repository, return the record + token."""

    def __init__(
        self,
        repository: AccessKeyRepository,
        signer: PrincipalSigner,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._signer = signer
        self._clock = clock

    async def issue(
        self, access_key: str, tenant: str, expire_at: datetime
    ) -> IssuedAccessKey:
        claims = {
            "iss": _ISSUER,
            "typ": "access_key",
            "sub": access_key,
            "tenant": tenant,
            "iat": int(self._clock()),
            "exp": int(expire_at.timestamp()),
            "jti": uuid.uuid4().hex,
        }
        token = await self._signer.sign_token(claims)
        await self._repository.store(
            token=token,
            access_key=access_key,
            tenant=tenant,
            expire_at=expire_at,
        )
        return IssuedAccessKey(
            access_key=access_key,
            tenant=tenant,
            expire_at=expire_at,
            token=token,
        )
