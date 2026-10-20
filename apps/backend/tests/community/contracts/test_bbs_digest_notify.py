"""Conformance suite for the BBS digest notification Plugin Protocol.

The local implementation is the executable offline spec: it must satisfy the
Protocol, return ``None`` for all channels, and never raise. The consumer-side
behavior is covered by the BBS digest report service tests.
"""

from __future__ import annotations

import pytest

from agentclaw.community.plugin_api.bbs_digest_notify import (
    BbsDigestNotifyProvider,
    NullBbsDigestNotifyProvider,
)
from agentclaw.community.plugin_api.notify_sender import NotifyMessage


@pytest.fixture
def message() -> NotifyMessage:
    return NotifyMessage(
        title="BBS sparkles",
        body="# BBS sparkles",
        recipient="staff123",
        deep_link="https://example.com/session",
        extra={"card_data": "{}"},
    )


def test_local_null_impl_satisfies_protocol() -> None:
    assert isinstance(NullBbsDigestNotifyProvider(), BbsDigestNotifyProvider)


def test_local_null_impl_returns_none_for_card_and_markdown(message) -> None:
    provider = NullBbsDigestNotifyProvider()
    assert provider.send(message, channel="tc_card") is None
    assert provider.send(message, channel="markdown") is None


def test_local_null_impl_never_raises_for_malformed_payload() -> None:
    provider = NullBbsDigestNotifyProvider()
    assert provider.send(None) is None  # type: ignore[arg-type]
