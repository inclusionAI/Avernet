"""Unit tests for AccessKeyIssuer (mints JWT, persists row, returns record)."""

from __future__ import annotations

from datetime import datetime

import jwt
import pytest

from gateway.community.bootstrap import initialize_database
from gateway.community.bootstrap._configs import DatabaseConfig
from gateway.community.core.access_key import AccessKeyIssuer, AccessKeyRepository
from gateway.community.core.access_key._issuer import IssuedAccessKey
from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)
from gateway.community.spi.database import DataSourcePlugin

_EXPIRE = datetime(2027, 1, 1, 0, 0, 0)
_FIXED_NOW = 1_700_000_000


@pytest.fixture
def db() -> DataSourcePlugin:
    return initialize_database(
        SqliteDatabasePlugin(), DatabaseConfig(plugin_type="SQLITE_ORM", db_url="")
    )


@pytest.fixture
def issuer(db: DataSourcePlugin) -> AccessKeyIssuer:
    return AccessKeyIssuer(
        AccessKeyRepository(db),
        BarePrincipalSigner(PrincipalSignerConfig(signing_key="k")),
        clock=lambda: _FIXED_NOW,
    )


async def test_issue_persists_row_and_returns_record(
    issuer: AccessKeyIssuer, db: DataSourcePlugin
) -> None:
    issued = await issuer.issue("ak-new", "t1", _EXPIRE)
    assert isinstance(issued, IssuedAccessKey)
    assert issued.access_key == "ak-new"
    assert issued.tenant == "t1"
    assert issued.expire_at == _EXPIRE
    assert issued.token

    rec = await AccessKeyRepository(db).find_access_key_by_token(issued.token)
    assert rec is not None
    assert rec.access_key == "ak-new"
    assert rec.tenant == "t1"
    assert rec.expire_at == _EXPIRE


async def test_issue_token_has_expected_claims(issuer: AccessKeyIssuer) -> None:
    issued = await issuer.issue("ak-new", "t1", _EXPIRE)
    decoded = jwt.decode(issued.token, "k", algorithms=["HS256"])
    assert decoded["typ"] == "access_key"
    assert decoded["sub"] == "ak-new"
    assert decoded["tenant"] == "t1"
    assert decoded["iat"] == _FIXED_NOW
    assert decoded["exp"] == int(_EXPIRE.timestamp())
    assert isinstance(decoded["jti"], str) and decoded["jti"]
