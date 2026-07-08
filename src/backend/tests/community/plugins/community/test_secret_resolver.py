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


def test_resolves_user_and_value_from_env(resolver, monkeypatch):
    monkeypatch.setenv(f"{_PREFIX}GIT_TOKEN_USER", "alice")
    monkeypatch.setenv(f"{_PREFIX}GIT_TOKEN_VALUE", "s3cr3t")
    secret = resolver.get_secret("git_token")
    assert isinstance(secret, _EnvSecret)
    assert secret.secret_user == "alice"
    assert secret.secret_value == "s3cr3t"


def test_name_is_normalized_uppercase_and_dashes(resolver, monkeypatch):
    # "my-git-token" → "MY_GIT_TOKEN"
    monkeypatch.setenv(f"{_PREFIX}MY_GIT_TOKEN_VALUE", "v")
    secret = resolver.get_secret("my-git-token")
    assert secret is not None
    assert secret.secret_value == "v"


def test_absent_secret_returns_none(resolver, monkeypatch):
    monkeypatch.delenv(f"{_PREFIX}MISSING_USER", raising=False)
    monkeypatch.delenv(f"{_PREFIX}MISSING_VALUE", raising=False)
    assert resolver.get_secret("missing") is None


def test_value_only_still_resolves_with_empty_user(resolver, monkeypatch):
    # A token-only secret (no user) is present; user defaults to "".
    monkeypatch.setenv(f"{_PREFIX}TOKEN_ONLY_VALUE", "tok")
    secret = resolver.get_secret("token_only")
    assert secret is not None
    assert secret.secret_user == ""
    assert secret.secret_value == "tok"


def test_user_only_without_value_returns_none(resolver, monkeypatch):
    # A half-set secret (user set, value missing) is treated as absent, not as a
    # present secret with an empty value — so consumers fall back rather than
    # run with an empty credential.
    monkeypatch.setenv(f"{_PREFIX}HALF_USER", "alice")
    monkeypatch.delenv(f"{_PREFIX}HALF_VALUE", raising=False)
    assert resolver.get_secret("half") is None


def test_prefix_isolates_lookup(monkeypatch):
    # A different prefix does not see another prefix's vars.
    monkeypatch.setenv("OTHER_PREFIX_FOO_VALUE", "x")
    resolver = CommunitySecretResolver(env_prefix="AGENTCLAW_SECRET_")
    assert resolver.get_secret("foo") is None
