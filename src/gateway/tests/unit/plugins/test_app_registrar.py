"""Unit tests for AppRegistrar (mints JWT, persists row, returns record)."""

from __future__ import annotations

import jwt
import pytest

from gateway.community.bootstrap._authn import build_database
from gateway.community.core.app import AppRegistrar, AppRepository
from gateway.community.core.app._registrar import IssuedApp
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)

_FIXED_NOW = 1_700_000_000


@pytest.fixture
def registrar() -> AppRegistrar:
    return AppRegistrar(
        AppRepository(build_database()),
        BarePrincipalSigner(PrincipalSignerConfig(signing_key="k")),
        clock=lambda: _FIXED_NOW,
    )


async def test_register_persists_row_and_returns_record(
    registrar: AppRegistrar,
) -> None:
    issued = await registrar.register("X App", "org-1", "assistant", "t1")
    assert isinstance(issued, IssuedApp)
    assert isinstance(issued.id, int) and issued.id >= 1
    assert issued.app_name == "X App"
    assert issued.owners == "org-1"
    assert issued.app_type == "assistant"
    assert issued.tenant == "t1"
    assert issued.token

    rec = await AppRepository(build_database()).find_app_by_token(issued.token)
    assert rec is not None
    assert rec.id == issued.id
    assert rec.app_name == "X App"
    assert rec.tenant == "t1"


async def test_register_token_has_expected_claims_and_no_exp(
    registrar: AppRegistrar,
) -> None:
    issued = await registrar.register("X App", "org-1", "assistant", "t1")
    decoded = jwt.decode(issued.token, "k", algorithms=["HS256"])
    assert decoded["typ"] == "app"
    assert decoded["sub"] == "X App"
    assert decoded["tenant"] == "t1"
    assert decoded["iat"] == _FIXED_NOW
    assert "exp" not in decoded
    assert isinstance(decoded["jti"], str) and decoded["jti"]
