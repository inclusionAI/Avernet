"""Conformance suite for NotifySenderPlugin (Rule 25).

Validates that local impl satisfies the NotifySenderPlugin Protocol
when injected via the ``world`` fixture.

These tests are scoped to the community profile where
``NoopNotifySender`` is bound.  Under ``corp_test`` the injector
provides ``DingTalkNotifySender`` instead, so the noop assertions
would not hold — that binding is exercised by the corp-side contract
tests instead.
"""
from __future__ import annotations

import os

import pytest

from agentclaw.community.plugin_api.notify_sender import (
    NotifyMessage,
    NotifySenderPlugin,
)

# Skip entire module when running under corp profiles — the world
# fixture binds DingTalkNotifySender there, not NoopNotifySender.
_allow_profiles = {"test", "community", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"NoopNotifySender contract only valid under {_allow_profiles}, "
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


def test_noop_send_returns_none(world, _sample_message):
    """NoopNotifySender.send() always returns None."""
    sender = world.get(NotifySenderPlugin)
    assert sender.send(_sample_message, channel="markdown") is None


def test_noop_channels_empty(world):
    """NoopNotifySender.channels is empty."""
    sender = world.get(NotifySenderPlugin)
    assert sender.channels == frozenset()


def test_noop_send_tc_card_returns_none(world, _sample_message):
    """NoopNotifySender.send() with tc_card channel also returns None."""
    sender = world.get(NotifySenderPlugin)
    assert sender.send(_sample_message, channel="tc_card") is None