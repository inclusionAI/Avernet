"""Unit tests for the ``dev_header`` user strategy (x-dev-user → UserPrincipal).

The strategy is the env-gated LOCAL-ONLY auth mock: it must resolve the header
only under ``GATEWAY_AUTH_MOCK=1`` and stay inert — even when chained — without
it. The gating tests for the bootstrap half live in ``test_strategy_chains.py``.
"""

from __future__ import annotations

import pytest

from gateway.community.plugins.authn.dev_header import (
    DEV_USER_HEADER,
    DevHeaderUserStrategy,
)
from gateway.community.spi.authn import CredentialBundle, UserPrincipal


def _creds(user: str | None) -> CredentialBundle:
    headers = {DEV_USER_HEADER: user} if user is not None else {}
    return CredentialBundle(headers=headers, cookies={}, query={})


async def test_enabled_header_resolves_user_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_AUTH_MOCK", "1")
    result = await DevHeaderUserStrategy().build(_creds("dev-alice"))
    assert isinstance(result, UserPrincipal)
    assert result.subject.id == "dev-alice"
    assert result.subject.username == "dev-alice"


async def test_disabled_env_ignores_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate 2: even a chained strategy answers None without the env var."""
    monkeypatch.delenv("GATEWAY_AUTH_MOCK", raising=False)
    assert await DevHeaderUserStrategy().build(_creds("dev-alice")) is None


async def test_non_one_value_is_not_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_AUTH_MOCK", "true")
    assert await DevHeaderUserStrategy().build(_creds("dev-alice")) is None


async def test_absent_header_is_not_applicable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enabled but headerless → None, so the runner still fail-closes."""
    monkeypatch.setenv("GATEWAY_AUTH_MOCK", "1")
    assert await DevHeaderUserStrategy().build(_creds(None)) is None


async def test_blank_header_is_not_applicable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GATEWAY_AUTH_MOCK", "1")
    assert await DevHeaderUserStrategy().build(_creds("   ")) is None


async def test_resolved_principal_asserts_no_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same contract as the google strategy: who, never which tenant."""
    monkeypatch.setenv("GATEWAY_AUTH_MOCK", "1")
    result = await DevHeaderUserStrategy().build(_creds("dev-alice"))
    assert isinstance(result, UserPrincipal)
    assert result.subject.tenant_id is None
