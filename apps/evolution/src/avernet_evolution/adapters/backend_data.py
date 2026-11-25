"""Wire contract for the internal, operator-scoped Evolution data callback.

Use a separate derived key and JWT purpose from Backend control receipts and
Gateway principals. Neither browser nor worker credentials are accepted.
"""

import hmac
import time

import jwt
from pydantic import BaseModel, ConfigDict, Field

from ..core.evolution_run.data_scope import DataScope

PREFIX = "/api/internal/evolution-data/v1/bots"
HEADER = "X-Avernet-Evolution-Data"
_TYPE = "avernet-evolution-data-v1"
_AUDIENCE = "backend:evolution-data"
_TTL = 60


class DataProof(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    tenant: str = Field(min_length=1)
    bot_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    owner_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run: str | None = None
    parent: str | None = None
    strategy: str | None = None
    genes: list[str] = Field(default_factory=list)


def _data_key(key: str) -> bytes:
    if len(key.encode()) < 32:
        raise ValueError("Evolution data trust is not configured")
    return hmac.digest(key.encode(), _TYPE.encode(), "sha256")


def issue_data_proof(key: str, tenant: str, scope: DataScope, request_hash: str) -> str:
    proof = DataProof(
        tenant=tenant,
        bot_id=scope.bot.bot_id,
        entity_id=scope.bot.entity_id,
        owner_id=scope.bot.owner_id,
        user_id=scope.user_id,
        request_hash=request_hash,
        run=scope.run_id,
        parent=scope.parent,
        strategy=scope.strategy,
        genes=list(scope.genes),
    )
    now = int(time.time())
    return jwt.encode(
        {"iss": "evolution", "aud": _AUDIENCE, "iat": now, "exp": now + _TTL, "data": proof.model_dump()},
        _data_key(key),
        algorithm="HS256",
        headers={"typ": _TYPE},
    )


def verify_data_proof(key: str, token: str) -> DataProof:
    try:
        if len(token) > 8192 or jwt.get_unverified_header(token).get("typ") != _TYPE:
            raise ValueError("wrong proof type")
        claims = jwt.decode(
            token,
            _data_key(key),
            algorithms=["HS256"],
            issuer="evolution",
            audience=_AUDIENCE,
            options={"require": ["iat", "exp", "iss", "aud", "data"], "strict_aud": True},
        )
        if (
            type(claims["iat"]) is not int
            or type(claims["exp"]) is not int
            or not 0 < claims["exp"] - claims["iat"] <= _TTL
        ):
            raise ValueError("wrong proof validity")
        return DataProof.model_validate(claims["data"])
    except (jwt.PyJWTError, ValueError, TypeError):
        raise PermissionError("Evolution data proof required") from None
