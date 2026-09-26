"""Shared pytest fixtures for the sandbox-proxy tests.

The proxy consumes HS256 JWTs that BaaS signs with a shared secret resolved from
``configs/application.yaml`` via ``${SANDBOXPROXY_JWT_SECRET:-}``. Rather than
have each test file hardcode its own secret, we establish a single default and
expose it through the ``jwt_secret`` fixture.
"""

from __future__ import annotations

import pytest

_DEFAULT_JWT_SECRET = "test-jwt-secret"


@pytest.fixture
def jwt_secret() -> str:
    """The shared HS256 secret used to sign/verify proxypass JWTs.

    Tests that build an app from a generated config should write this same value
    into their ``user_config.jwt.secret``. Tests that load the repo's
    ``configs/application.yaml`` get it for free via the autouse env fixture.
    """
    return _DEFAULT_JWT_SECRET


@pytest.fixture(autouse=True)
def _default_jwt_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set a default ``SANDBOXPROXY_JWT_SECRET`` for the whole test process.

    ``monkeypatch`` restores the previous value after each test, so a test may
    still override the secret with its own ``monkeypatch.setenv`` or by writing
    an explicit ``jwt.secret`` into a generated config.
    """
    monkeypatch.setenv("SANDBOXPROXY_JWT_SECRET", _DEFAULT_JWT_SECRET)
