"""Conformance suite for NotifyMessagesProvider (Rule 25).

Validates that the local Null impl satisfies the NotifyMessagesProvider
Protocol. The narrow port carries the ``send``-never-raise contract shared
with ``NotifySenderPlugin``.

Under community/singlebox/test profiles the ``world`` fixture binds
``DingTalkNotifySender(CommunityNotifySender)`` via ``CommunityNotifyModule``
(log + optional DingTalk card). The Null impl is a separate ``@plugin_impl``
class registered under ``plugins/local/``; these tests exercise it directly
rather than via the DI ``world`` fixture.
"""
from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.notify_sender import NotifyMessage
from agentclaw.community.plugins.local.task_discovery_notify import (
    NullNotifyMessagesProvider,
)


@pytest.fixture
def _sample_message() -> NotifyMessage:
    return NotifyMessage(
        title="test title",
        body="# test body",
        recipient="staff123",
        deep_link="https://example.com/detail",
    )


def test_null_send_returns_none(_sample_message):
    """Null impl: send() returns None (noop, never raises)."""
    provider = NullNotifyMessagesProvider()
    assert provider.send(_sample_message, channel="markdown") is None


def test_null_impl_satisfies_protocol():
    """Null impl satisfies the Plugin Protocol (runtime_checkable)."""
    provider = NullNotifyMessagesProvider()
    from agentclaw.community.plugin_api.task_discovery_notify import (
        NotifyMessagesProvider,
    )
    assert isinstance(provider, NotifyMessagesProvider)


def test_null_send_all_channels_noop(_sample_message):
    """Null impl: any channel (markdown / tc_card) is a noop -> None."""
    provider = NullNotifyMessagesProvider()
    assert provider.send(_sample_message, channel="tc_card") is None
