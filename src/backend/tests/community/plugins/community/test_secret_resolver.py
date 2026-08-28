"""Unit tests for the community CommunitySecretResolver (B3)."""
from __future__ import annotations

import pytest

from agentclaw.community.plugins.community.secret_resolver import (
    CommunitySecretResolver,
    _EnvSecret,
)

_PREFIX = "AGENTCLAW_SECRET_"


@pytest.fixture
def resolver() -> CommunitySecretResolver:
    return CommunitySecretResolver(env_prefix=_PREFIX)


def test_resolves_value_from_env(resolver, monkeypatch):
    monkeypatch.setenv(f"{_PREFIX}git_token", "s3cr3t")
    secret = resolver.get_secret("git_token")
    assert isinstance(secret, _EnvSecret)
    assert secret.secret_value == "s3cr3t"
    assert secret.secret_user == ""


def test_resolves_with_prefix(resolver, monkeypatch):
    monkeypatch.setenv(f"{_PREFIX}my_secret", "val")
    secret = resolver.get_secret("my_secret")
    assert secret.secret_value == "val"


def test_absent_secret_returns_none(resolver, monkeypatch):
    monkeypatch.delenv(f"{_PREFIX}missing", raising=False)
    assert resolver.get_secret("missing") is None


def test_prefix_isolates_lookup(monkeypatch):
    monkeypatch.setenv("OTHER_PREFIX_foo", "x")
    resolver = CommunitySecretResolver(env_prefix="AGENTCLAW_SECRET_")
    assert resolver.get_secret("foo") is None
