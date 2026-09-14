"""Conformance suite for FrontendUrlProvider (Rule 25).

Validates that the local Null impl satisfies the FrontendUrlProvider Protocol
when injected via the ``world`` fixture (consumer ↔ Protocol conformance: the
bound provider is the executable spec for the empty-string fallback contract).

Under community/singlebox/test profiles the Null impl (empty string →
downstream constructor default ``http://localhost:8000``) is bound. Under
``corp_test`` the injector wires ``CorpFrontendUrlProvider`` instead,
exercised by corp-side contract tests.
"""
from __future__ import annotations

import os

import pytest

from agentclaw.community.plugin_api.frontend_url import FrontendUrlProvider

# Skip under corp profiles — the world fixture binds the corp provider there.
_allow_profiles = {"test", "community", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"Null FrontendUrlProvider contract only valid under {_allow_profiles}, "
    f"got DEPLOY_PROFILE={_current_profile!r}",
)


def test_local_impl_returns_empty_string(world):
    """Null impl: get() returns "" (downstream falls back to ctor default)."""
    provider = world.get(FrontendUrlProvider)
    assert provider.get() == ""


def test_local_impl_is_runtime_checkable(world):
    """The bound provider satisfies the Plugin Protocol (runtime_checkable)."""
    provider = world.get(FrontendUrlProvider)
    assert isinstance(provider, FrontendUrlProvider)


def test_local_impl_never_raises(world):
    """Null impl get() is stable across repeated calls (no hidden state)."""
    provider = world.get(FrontendUrlProvider)
    assert provider.get() == provider.get() == ""