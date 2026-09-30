from __future__ import annotations

import pytest

from gateway.community.plugins.authn.dev_cookie import DevCookieUserStrategy
from gateway.community.spi.authn import CredentialBundle, PrincipalType


@pytest.mark.asyncio
async def test_dev_cookie_strategy_builds_user_from_staff_cookie(monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "local")
    strategy = DevCookieUserStrategy()

    principal = await strategy.build(
        CredentialBundle(
            headers={},
            cookies={"staff_id": "334018", "nick_name": "%E5%BC%80%E5%8F%91%E8%80%85"},
            query={},
        )
    )

    assert principal is not None
    assert principal.type is PrincipalType.USER
    assert principal.subject.id == "334018"
    assert principal.subject.username == "334018"
    assert principal.subject.display_name == "开发者"


@pytest.mark.asyncio
async def test_dev_cookie_strategy_is_inert_outside_enabled_envs(monkeypatch) -> None:
    monkeypatch.setenv("SERVER_ENV", "prod")
    strategy = DevCookieUserStrategy()

    principal = await strategy.build(
        CredentialBundle(headers={}, cookies={"staff_id": "334018"}, query={})
    )

    assert principal is None
