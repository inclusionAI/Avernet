"""Unit tests for the community entrypoint helpers + logger registry.

Covers the corp-free boot helper (``_entry.ensure_stdin_fd``), the profile-driven
logger registry (``log.set_logger_factory`` / ``get_logger``), and the community
entrypoint's profile gate (``main._require_local_profile``) — the logic that
rejects a corp profile and routes every community/local profile to the uvicorn
boot.
"""
from __future__ import annotations

import logging
import sys

import pytest

from agentclaw.community.di.profile import DeployProfile


def test_ensure_stdin_fd_reinitialises_fd0(monkeypatch):
    import agentclaw.community._entry as entry

    calls: dict[str, object] = {}
    monkeypatch.setattr(entry.os, "open", lambda path, flags: 7)
    monkeypatch.setattr(entry.os, "dup2", lambda a, b: calls.__setitem__("dup2", (a, b)))
    monkeypatch.setattr(entry.os, "close", lambda fd: calls.__setitem__("close", fd))
    monkeypatch.setattr(entry.os, "fdopen", lambda fd, mode: f"stream-{fd}-{mode}")
    # The function reassigns sys.stdin; record the original so monkeypatch restores it.
    monkeypatch.setattr(sys, "stdin", sys.stdin)

    entry.ensure_stdin_fd()

    assert calls["dup2"] == (7, 0)  # duped onto fd 0
    assert calls["close"] == 7      # temp fd closed
    assert sys.stdin == "stream-0-r"  # stdin rebound to the fresh fd 0


def test_logger_factory_default_is_stdlib():
    from agentclaw.community import log

    assert isinstance(log.get_logger("x"), logging.Logger)


def test_set_logger_factory_overrides_default(monkeypatch):
    from agentclaw.community import log

    # Record the original so monkeypatch restores it (avoid cross-test leak).
    monkeypatch.setattr(log, "_factory", log._factory)

    sentinel = object()
    log.set_logger_factory(lambda name: sentinel)
    assert log.get_logger("anything") is sentinel


def test_require_local_profile_accepts_every_community_profile():
    from agentclaw.community import main

    for profile in (
        DeployProfile.SINGLEBOX,
        DeployProfile.TEST,
        DeployProfile.CORP_TEST,
        DeployProfile.COMMUNITY,
    ):
        # Does not raise.
        main._require_local_profile(profile)


def test_require_local_profile_rejects_corp():
    from agentclaw.community import main

    with pytest.raises(RuntimeError, match="agentclaw/corp/main.py"):
        main._require_local_profile(DeployProfile.CORP)
