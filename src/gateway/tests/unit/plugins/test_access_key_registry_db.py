"""DB-backed tests for ``AccessKeyRepository`` (seeded ``access_keys`` table)."""

from __future__ import annotations

from datetime import datetime

import pytest

from gateway.community.bootstrap._authn import build_database
from gateway.community.core.access_key import AccessKeyRepository
from gateway.community.spi.access_key import RegisteredAccessKey


@pytest.fixture(scope="module")
def registry() -> AccessKeyRepository:
    return AccessKeyRepository(build_database())


async def test_known_token_resolves_seeded_access_key(
    registry: AccessKeyRepository,
) -> None:
    ak = await registry.find_access_key_by_token("ak-token")
    assert ak == RegisteredAccessKey(
        access_key_id="ak-1",
        tenant="t",
        expire_at=datetime(2027, 1, 1, 0, 0, 0),
    )


async def test_unknown_token_returns_none(
    registry: AccessKeyRepository,
) -> None:
    assert await registry.find_access_key_by_token("nope") is None
