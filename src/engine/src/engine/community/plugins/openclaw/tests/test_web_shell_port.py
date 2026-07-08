"""Port-impl tests for OpenClaw web_shell transport.

Preserves the legacy engines/openclaw/tests/test_web_shell.py coverage: the
check_token HMAC logic (allow-all when no DEBUG_TOKEN) and the PTY session
close()-idempotency. No real fork/PTY — os calls are monkeypatched. The adapter's
auth-bridge is covered by core/adapters/openclaw/tests/test_web_shell.py.
"""
from __future__ import annotations

import os

from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl
from engine.community.plugins.openclaw.web_shell import OpenClawWebShellSession


class TestCheckToken:
    def test_allows_all_when_no_debug_token_configured(self, monkeypatch):
        monkeypatch.delenv("DEBUG_TOKEN", raising=False)
        assert OpenClawPluginImpl().check_token("anything") is True

    def test_matches_configured_token(self, monkeypatch):
        monkeypatch.setenv("DEBUG_TOKEN", "secret")
        impl = OpenClawPluginImpl()
        assert impl.check_token("secret") is True
        assert impl.check_token("wrong") is False

    def test_reads_debug_token_env_lazily_at_call_time(self, monkeypatch):
        # The impl reads DEBUG_TOKEN at check_token() call time, not at
        # construction — so an env set AFTER construction still takes effect.
        monkeypatch.delenv("DEBUG_TOKEN", raising=False)
        impl = OpenClawPluginImpl()  # constructed with no token configured
        monkeypatch.setenv("DEBUG_TOKEN", "later")  # set after construction
        assert impl.check_token("later") is True
        assert impl.check_token("nope") is False


class TestSessionClose:
    def test_close_kills_child_and_closes_fd(self, monkeypatch):
        calls: list[tuple] = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)))
        monkeypatch.setattr(os, "waitpid", lambda pid, flags: calls.append(("waitpid", pid, flags)))
        monkeypatch.setattr(os, "close", lambda fd: calls.append(("close", fd)))

        sess = OpenClawWebShellSession(master_fd=7, child_pid=123)
        sess.close()
        assert ("kill", 123, 9) in calls
        assert ("close", 7) in calls

    def test_close_is_idempotent(self, monkeypatch):
        n = {"kill": 0, "close": 0}
        monkeypatch.setattr(os, "kill", lambda pid, sig: n.__setitem__("kill", n["kill"] + 1))
        monkeypatch.setattr(os, "waitpid", lambda pid, flags: None)
        monkeypatch.setattr(os, "close", lambda fd: n.__setitem__("close", n["close"] + 1))

        sess = OpenClawWebShellSession(master_fd=7, child_pid=123)
        sess.close()
        sess.close()  # second close must be a no-op (the _closed guard)
        assert n["kill"] == 1
        assert n["close"] == 1

    def test_close_swallows_oserror(self, monkeypatch):
        def _boom(*a):
            raise OSError("gone")

        monkeypatch.setattr(os, "kill", _boom)
        monkeypatch.setattr(os, "waitpid", lambda pid, flags: None)
        monkeypatch.setattr(os, "close", _boom)
        # must not raise
        OpenClawWebShellSession(master_fd=7, child_pid=123).close()

    def test_write_noop_after_close(self, monkeypatch):
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(os, "waitpid", lambda pid, flags: None)
        monkeypatch.setattr(os, "close", lambda fd: None)
        writes: list = []
        monkeypatch.setattr(os, "write", lambda fd, data: writes.append((fd, data)))

        sess = OpenClawWebShellSession(master_fd=7, child_pid=123)
        sess.close()
        sess.write(b"hello")  # fd is None after close → no os.write
        assert writes == []
