from __future__ import annotations

import json
import time
from pathlib import Path

import jwt
from pydantic import TypeAdapter

from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)
from gateway.community.spi.authn import Principal

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE_PATH = (
    _REPO_ROOT
    / "src/bcs/api-contracts/v1/gateway-principal/principal-set.json"
)
_TEST_ONLY_KEY = "TEST-ONLY-bcs-principal-contract-key-32-bytes"


async def test_gateway_serialization_matches_bcs_principal_contract() -> None:
    raw = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    principals = TypeAdapter(list[Principal]).validate_python(raw["principals"])
    serialized = [principal.model_dump(mode="json") for principal in principals]
    assert serialized == raw["principals"]

    now = int(time.time())
    signer = BarePrincipalSigner(
        PrincipalSignerConfig(
            signing_key=_TEST_ONLY_KEY,
            kid=raw["key_id"],
            issuer=raw["issuer"],
            ttl_seconds=60,
        ),
        clock=lambda: now,
    )
    token = await signer.sign(
        {principal.type: principal for principal in principals},
        audience=raw["audience"],
    )

    header = jwt.get_unverified_header(token)
    assert header == {"alg": "HS256", "kid": "bare", "typ": "JWT"}
    claims = jwt.decode(
        token,
        _TEST_ONLY_KEY,
        algorithms=["HS256"],
        audience="bcs",
        issuer="gateway",
    )
    assert claims["iat"] == now
    assert claims["exp"] == now + 60
    assert claims["principals"] == raw["principals"]
