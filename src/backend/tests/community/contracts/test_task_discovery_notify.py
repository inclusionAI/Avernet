"""Conformance suite for NotifyMessagesProvider (Rule 25).

Validates that the local Null impl satisfies the NotifyMessagesProvider
Protocol when injected via the ``world`` fixture. The narrow port carries the
``send``-never-raise contract shared with ``NotifySenderPlugin``.

Under community/singlebox/test profiles the Null impl (send → None) is
bound. Under ``corp_test`` the injector wires ``CorpNotifyMessagesProvider``
(env-aware DingTalk) instead, exercised by corp-side contract tests.
"""
from __future__ import annotations

import os

import pytest

from agentclaw.community.plugin_api.notify_sender import NotifyMessage
from agentclaw.community.plugin_api.task_discovery_notify import (
    NotifyMessagesProvider,
)

# Skip under corp profiles — the world fixture binds the corp provider there.
_allow_profiles = {"test", "community", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"Null NotifyMessagesProvider contract only valid under {_allow_profiles}, "
    f"got DEPLOY_PROFILE={_current_profile!r}",
)


@pytest.fixture
def _sample_message() -> NotifyMessage:
    return NotifyMessage(
        title="test title",
        body="# test body",
        recipient="staff123",
        deep_link="https://example.com/detail",
    )


def test_local_send_returns_none(world, _sample_message):
    """Null impl: send() returns None (noop, never raises)."""
    provider = world.get(NotifyMessagesProvider)
    assert provider.send(_sample_message, channel="markdown") is None


def test_local_send_validates_protocol(world):
    """The bound provider satisfies the Plugin Protocol (runtime_checkable)."""
    provider = world.get(NotifyMessagesProvider)
    assert isinstance(provider, NotifyMessagesProvider)


def test_local_send_all_channels_noop(world, _sample_message):
    """Null impl: any channel (markdown / tc_card) is a noop → None."""
    provider = world.get(NotifyMessagesProvider)
    assert provider.send(_sample_message, channel="tc_card") is None