"""Tests for AuthPlugin protocol compliance.

Uses the registered ``LocalAuth`` fake (Rule 21 isolation impl) instead
of a hand-rolled stub — proves the registry is fit-for-purpose.
"""
from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugins.local.auth import LocalAuth


class _NonConformingAuth:
    """Class missing required methods."""
    pass


def test_conforming_class_is_auth_plugin():
    auth: AuthPlugin = LocalAuth()
    assert isinstance(auth, AuthPlugin)


def test_non_conforming_class_is_not_auth_plugin():
    obj = _NonConformingAuth()
    assert not isinstance(obj, AuthPlugin)
