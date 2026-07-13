"""Conformance suite for NotifySenderPlugin (Rule 25).

Validates that the community impl satisfies the NotifySenderPlugin Protocol
when injected via the ``world`` fixture.

Under community/singlebox/test profiles, ``CommunityNotifySender`` (log
channel) is bound.  Under ``corp_test`` the injector provides
``DingTalkNotifySender`` instead, exercised by corp-side contract tests.
"""
from __future__ import annotations

import os

import pytest

from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

# Skip entire module when running under corp profiles — the world
# fixture binds DingTalkNotifySender there, not CommunityNotifySender.
_allow_profiles = {"test", "community", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"CommunityNotifySender contract only valid under {_allow_profiles}, "
    f"got DEPLOY_PROFILE={_current_profile!r}",
)


@pytest.fixture
def _sample_message() -> NotifyMessage:
    return NotifyMessage(
        title="test title",
        body="# test body",
        recipient="staff123",
        deep_link="https://example.com/detail",
        extra={"bot_id": "bot_001"},
    )


def test_community_send_returns_id(world, _sample_message):
    """CommunityNotifySender.send() returns a message ID (log delivery succeeds)."""
    sender = world.get(NotifySenderPlugin)
    result = sender.send(_sample_message, channel="log")
    assert result is not None
    assert result.startswith("log-")


def test_community_channels_includes_log(world):
    """CommunityNotifySender.channels includes 'log'."""
    sender = world.get(NotifySenderPlugin)
    assert "log" in sender.channels


def test_community_send_unsupported_channel_falls_back(world, _sample_message):
    """CommunityNotifySender.send() with unsupported channel falls back to log."""
    sender = world.get(NotifySenderPlugin)
    result = sender.send(_sample_message, channel="markdown")
    # Falls back to log channel — still succeeds with a log- ID
    assert result is not None